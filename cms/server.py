#!/usr/bin/env python3
"""Kleines CMS fuer die Hugo-Seite.

Bearbeitet die Markdown-Dateien in den Unterordnern von website/content/.
Welche Ordner das sind, steht in cms.toml neben dem content-Ordner.

Start:  python3 cms/server.py
        python3 cms/server.py --port 8000 --content /srv/kunde-a/website/content
"""

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import threading
import tomllib
import traceback
import unicodedata
from datetime import date
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:          # ohne Pillow laeuft alles ausser dem Bild-Upload
    Image = None

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_CONTENT_DIR = BASE_DIR.parent / "website" / "content"

# Welche Sektionen bearbeitbar sind, steht in cms.toml neben dem content-Ordner.
CONFIG_NAME = "cms.toml"

# Nach jedem Schreiben laeuft Hugo. Fuer diese Seite dauert das ~60 ms, darum
# synchron: die Antwort sagt dann schon, ob die Seite wirklich neu steht.
BUILD_TIMEOUT = 60

# Commit laeuft im Request und ist rein lokal. Der Push geht ins Netz und
# laeuft deshalb nebenher -- er darf das Speichern des Kunden nie aufhalten.
GIT_TIMEOUT = 15
PUSH_TIMEOUT = 120

# Nur einfache Dateinamen, kein Pfadwechsel, kein Listenindex des Abschnitts.
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\.md$")
SAFE_DIR = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RESERVED = {"_index.md", "index.md"}

# Bilder: hochgeladen wird in static/uploads/<jahr>/, ausgeliefert unter /uploads/.
# static/ kopiert Hugo unveraendert, das Bild ist also ohne Zutun auf der Seite.
UPLOAD_DIR = "uploads"
UPLOAD_MAX_BYTES = 25 * 1024 * 1024
BILD_BREITE = 2000          # laengere Kante, reicht fuer jeden Bildschirm
BILD_QUALITAET = 80         # daraus werden aus 1,1 MB rund 200 KB
UPLOAD_ROUTE = re.compile(r"^/uploads/(\d{4})/([a-z0-9][a-z0-9._-]*\.webp)$")

# /api/sections/<id>/entries[/<datei>]
ENTRIES_ROUTE = re.compile(r"^/api/sections/([a-z0-9][a-z0-9_-]*)/entries(?:/([^/]+))?$")
# Muss zum Sektionsteil von ENTRIES_ROUTE passen, sonst ist die Sektion nicht erreichbar.
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

UMLAUTE = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


class PostError(Exception):
    """Fehler, der als 400 an die Oberflaeche zurueckgeht."""


def load_config(path: Path) -> tuple[str, list[dict]]:
    """Liest cms.toml und prueft sie.

    Alles, was hier schiefgeht, bricht den Start ab: eine kaputte
    Konfiguration soll beim Hochfahren auffallen, nicht beim ersten Klick.
    """
    try:
        with path.open("rb") as datei:
            daten = tomllib.load(datei)
    except FileNotFoundError:
        raise SystemExit(f"Konfiguration nicht gefunden: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: kein gueltiges TOML -- {exc}")

    titel = str(daten.get("title") or "").strip() or "CMS"
    roh = daten.get("sections")
    if not isinstance(roh, list) or not roh:
        raise SystemExit(f"{path}: mindestens eine [[sections]]-Tabelle noetig.")

    sections: list[dict] = []
    vergeben: set[str] = set()
    for nummer, eintrag in enumerate(roh, 1):
        stelle = f"{path}: [[sections]] Nr. {nummer}"
        if not isinstance(eintrag, dict):
            raise SystemExit(f"{stelle} ist keine Tabelle.")
        ordner = str(eintrag.get("dir") or "").strip()
        if not SAFE_DIR.fullmatch(ordner):
            raise SystemExit(
                f"{stelle}: dir fehlt oder ist unzulaessig (klein, ohne "
                f"Schraegstrich): {ordner!r}"
            )
        kennung = str(eintrag.get("id") or ordner).strip()
        if not SAFE_ID.fullmatch(kennung):
            raise SystemExit(f"{stelle}: id ist unzulaessig: {kennung!r}")
        if kennung in vergeben:
            raise SystemExit(f"{stelle}: id kommt mehrfach vor: {kennung!r}")
        vergeben.add(kennung)
        sections.append({
            "id": kennung,
            "name": str(eintrag.get("name") or ordner).strip(),
            "dir": ordner,
        })
    return titel, sections


def slugify(title: str) -> str:
    text = title.strip().lower()
    for umlaut, ersatz in UMLAUTE.items():
        text = text.replace(umlaut, ersatz)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "eintrag"


def parse_front_matter(raw: str) -> tuple[dict, list[str], str]:
    """Trennt Frontmatter und Text.

    Rueckgabe: bekannte Felder, unveraenderte Restzeilen, Fliesstext.
    """
    fields: dict[str, str | bool] = {}
    extra: list[str] = []

    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return fields, extra, raw

    # Erste Zeile, die nur aus --- besteht, schliesst den Frontmatter ab.
    # Zeilenweise, damit --- im Fliesstext (z. B. im Codeblock) nichts abschneidet.
    try:
        ende = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return fields, extra, raw

    head_lines = lines[1:ende]
    body = "\n".join(lines[ende + 1:]).lstrip("\n")

    for line in head_lines:
        key, sep, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not sep or key not in ("title", "date", "draft"):
            if line.strip():
                extra.append(line)
            continue
        if key == "draft":
            fields[key] = value.lower() == "true"
        else:
            fields[key] = value

    return fields, extra, body


def build_front_matter(title: str, datum: str, draft: bool, extra: list[str], body: str) -> str:
    lines = ["---", f'title: "{title}"', f"date: {datum}"]
    if draft:
        lines.append("draft: true")
    lines.extend(extra)
    lines.append("---")
    text = "\n".join(lines) + "\n\n" + body.strip()
    return text.rstrip() + "\n"


def hugo_bauen(site_dir: Path, hugo: str) -> dict:
    """Baut die Seite neu.

    Hugo meldet Fehler ueber den Exit-Code und schreibt den Text nach stdout
    (nicht stderr), mit "Error:" am Zeilenanfang. --quiet verschluckt ihn,
    deshalb laeuft Hugo hier ohne.

    --cleanDestinationDir ist noetig, damit ein geloeschter Beitrag auch aus
    public/ verschwindet -- sonst bliebe seine Seite fuer Besucher erreichbar.
    """
    try:
        lauf = subprocess.run(
            [hugo, "--cleanDestinationDir"], cwd=site_dir,
            capture_output=True, text=True, timeout=BUILD_TIMEOUT,
        )
    except FileNotFoundError:
        return {"ok": False, "meldung": f"Hugo nicht gefunden: {hugo}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "meldung": f"Hugo antwortet seit {BUILD_TIMEOUT} s nicht."}
    except OSError as exc:
        return {"ok": False, "meldung": f"Hugo liess sich nicht starten: {exc}"}

    if lauf.returncode == 0:
        return {"ok": True, "meldung": ""}

    ausgabe = f"{lauf.stdout}\n{lauf.stderr}"
    zeile = next((z for z in ausgabe.splitlines() if z.startswith("Error:")), "")
    # Absolute Pfade kuerzen -- fuer den Kunden ist der Dateiname genug.
    meldung = zeile.replace(f"{site_dir}/", "") or f"Hugo endete mit Code {lauf.returncode}."
    return {"ok": False, "meldung": meldung, "ausgabe": ausgabe.strip()}


def git_lauf(repo: Path, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    # LC_ALL=C: sonst antwortet git in der Sprache des Servers, und Meldungen
    # zu lesen wird zum Ratespiel.
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "LC_ALL": "C"},
    )


def erste_zeile(lauf: subprocess.CompletedProcess, ersatz: str) -> str:
    """Die erste Zeile nennt den Grund; die letzte ist meist nur ein Nachsatz."""
    for zeile in f"{lauf.stderr}\n{lauf.stdout}".splitlines():
        if zeile.strip():
            return zeile.strip()
    return ersatz


def git_repo(start: Path) -> Path | None:
    """Wurzel des Repos, in dem die Inhalte liegen -- oder None."""
    try:
        lauf = git_lauf(start, ["rev-parse", "--show-toplevel"], GIT_TIMEOUT)
    except FileNotFoundError:
        raise SystemExit("--git ist gesetzt, aber git ist nicht installiert.")
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(lauf.stdout.strip()) if lauf.returncode == 0 else None


def git_commit(repo: Path, pfade: list[Path], nachricht: str,
               name: str, email: str) -> dict:
    """Legt einen Commit an. Identitaet kommt mit, damit das Repo keine braucht."""
    try:
        git_lauf(repo, ["add", "--", *(str(p) for p in pfade)], GIT_TIMEOUT)
        # Nichts vorgemerkt heisst: der Text war unveraendert. Das ueber den
        # Rueckgabewert pruefen statt ueber den Meldungstext -- der haengt
        # von der Sprache ab.
        steht_an = git_lauf(repo, ["diff", "--cached", "--quiet", "--",
                                   *(str(p) for p in pfade)], GIT_TIMEOUT)
        if steht_an.returncode == 0:
            return {"ok": True, "meldung": ""}
        lauf = git_lauf(repo, [
            "-c", f"user.name={name}", "-c", f"user.email={email}",
            "commit", "-m", nachricht, "--", *(str(p) for p in pfade),
        ], GIT_TIMEOUT)
    except FileNotFoundError:
        return {"ok": False, "meldung": "git nicht gefunden."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "meldung": f"git antwortet seit {GIT_TIMEOUT} s nicht."}
    except OSError as exc:
        return {"ok": False, "meldung": f"git liess sich nicht starten: {exc}"}

    if lauf.returncode == 0:
        return {"ok": True, "meldung": ""}
    return {"ok": False, "meldung": erste_zeile(
        lauf, f"git commit endete mit Code {lauf.returncode}.")}


def git_push(repo: Path) -> dict:
    try:
        lauf = git_lauf(repo, ["push"], PUSH_TIMEOUT)
    except FileNotFoundError:
        return {"ok": False, "meldung": "git nicht gefunden."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "meldung": f"Push seit {PUSH_TIMEOUT} s ohne Antwort."}
    except OSError as exc:
        return {"ok": False, "meldung": f"Push liess sich nicht starten: {exc}"}
    if lauf.returncode == 0:
        return {"ok": True, "meldung": ""}
    return {"ok": False, "meldung": erste_zeile(lauf, "Push fehlgeschlagen.")}


def schreibe_atomar(path: Path, text: str) -> None:
    """Erst daneben schreiben, dann umhaengen.

    os.replace ist auf einem Dateisystem unteilbar. Ohne das koennte ein
    gleichzeitiger Leser -- das Laden der Liste etwa -- eine halb
    geschriebene Datei erwischen.

    Der Zwischenname beginnt mit einem Punkt und endet nicht auf .md, faellt
    also weder durch glob("*.md") noch Hugo auf.
    """
    zwischen = path.with_name(f".{path.name}.tmp")
    try:
        zwischen.write_text(text, encoding="utf-8")
        os.replace(zwischen, path)
    except BaseException:
        zwischen.unlink(missing_ok=True)
        raise


def bild_umwandeln(rohdaten: bytes) -> tuple[bytes, int, int]:
    """Macht aus beliebigen Bilddaten ein verkleinertes WebP.

    Das Neuberechnen ist zugleich die Pruefung: Was Pillow nicht als Bild
    oeffnen kann, fliegt raus, und Verstecktes ueberlebt es nicht. Auch die
    EXIF-Daten verschwinden dabei -- in Handyfotos steht dort oft der
    Aufnahmeort.
    """
    if Image is None:
        raise PostError("Bild-Upload braucht Pillow (pip install Pillow).")
    try:
        bild = Image.open(io.BytesIO(rohdaten))
        bild.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # Haeufigster echter Fall: HEIC vom iPhone, das Pillow ohne Zusatz
        # nicht oeffnet. Der Rest sind kaputte oder untergeschobene Dateien.
        raise PostError(
            "Die Datei liess sich nicht als Bild oeffnen. Moeglich ist ein "
            "nicht unterstuetztes Format wie HEIC von neueren iPhones -- "
            "dann beim Export JPEG waehlen."
        ) from exc

    # Handyfotos stehen oft nur ueber ein EXIF-Feld richtig herum.
    bild = ImageOps.exif_transpose(bild)
    bild = bild.convert("RGBA" if "A" in bild.getbands() else "RGB")
    bild.thumbnail((BILD_BREITE, BILD_BREITE), Image.LANCZOS)

    ziel = io.BytesIO()
    bild.save(ziel, "WEBP", quality=BILD_QUALITAET, method=4)
    return ziel.getvalue(), bild.width, bild.height


def read_entry(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # Kommt vor, wenn eine Datei am CMS vorbei angelegt wurde, etwa aus
        # einem alten Editor in Latin-1.
        raise PostError(
            f"{path.name} ist nicht in UTF-8 gespeichert und laesst sich "
            f"nicht lesen."
        ) from exc
    fields, _extra, body = parse_front_matter(raw)
    return {
        "file": path.name,
        "title": fields.get("title") or path.stem.replace("-", " ").title(),
        "date": fields.get("date") or "",
        "draft": bool(fields.get("draft")),
        "body": body,
    }


# Im Frontmatter steht der Titel als title: "...". Dort beendet " den Wert
# vorzeitig, und \ leitet eine Escape-Sequenz ein (\b waere ein Steuerzeichen).
TITEL_VERBOTEN = '"\\'


def check_title(title: str) -> None:
    """Titel muss in title: "..." passen, sonst bricht Hugos Frontmatter."""
    if not title:
        raise PostError("Titel fehlt.")
    schlecht = [zeichen for zeichen in TITEL_VERBOTEN if zeichen in title]
    if schlecht:
        hinweis = ' Stattdessen \u201e \u201c oder \u00bb \u00ab nehmen.' if '"' in title else ""
        raise PostError(
            f'Titel darf kein {" und ".join(schlecht)} enthalten \u2014 Hugo kann '
            f'den Frontmatter sonst nicht lesen.{hinweis}'
        )


def payload_to_values(data: dict) -> tuple[str, str, bool, str]:
    title = str(data.get("title", "")).strip()
    check_title(title)
    datum = str(data.get("date", "")).strip() or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum):
        raise PostError("Datum muss im Format JJJJ-MM-TT stehen.")
    return title, datum, bool(data.get("draft")), str(data.get("body", ""))


class CMSHandler(SimpleHTTPRequestHandler):
    # Ein Prozess bedient eine Seite; main() setzt diese aus cms.toml.
    content_dir: Path = DEFAULT_CONTENT_DIR
    sections: list[dict] = []
    site_title: str = "CMS"
    site_dir: Path = DEFAULT_CONTENT_DIR.parent   # hier liegt hugo.toml
    hugo: str | None = None                       # None = nicht bauen
    repo: Path | None = None                      # None = nicht versionieren
    git_name: str = "CMS"
    git_email: str = "cms@localhost"
    push: bool = False
    # Der Push laeuft nebenher; sein Ergebnis kommt mit der naechsten Antwort
    # mit, damit eine klemmende Sicherung nicht unbemerkt bleibt.
    letzter_push: dict | None = None
    push_sperre = threading.Lock()

    # Schreiben, Bauen und Committen vertragen sich nicht nebenlaeufig:
    # zwei Hugo-Laeufe raeumen einander public/ weg, zwei git-Commits
    # streiten um index.lock, und free_path() koennte zweimal denselben
    # Dateinamen vergeben. Lesende Anfragen laufen weiter frei.
    schreib_sperre = threading.Lock()
    # Laeuft schon ein Push, wird kein zweiter gestartet -- der laufende
    # holt die neuen Commits in einer weiteren Runde mit.
    push_lauf = threading.Lock()
    push_nochmal = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    # --- Hilfen -------------------------------------------------------
    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        # Ein fremdes <form> kann nur text/plain und Formulardaten schicken.
        # application/json erzwingt beim Browser eine Rueckfrage (CORS), die
        # dieser Server nie beantwortet -- so kommt nichts Fremdes durch.
        art = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if art != "application/json":
            raise PostError("Anfrage muss application/json sein.")
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            raise PostError("Leere Anfrage.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PostError(f"Ungueltiges JSON: {exc}") from exc

    def section(self, section_id: str) -> dict:
        for eintrag in self.sections:
            if eintrag["id"] == section_id:
                return eintrag
        raise FileNotFoundError(f"Sektion nicht gefunden: {section_id}")

    def section_dir(self, section_id: str) -> Path:
        ordner = self.section(section_id)["dir"]
        if not SAFE_DIR.fullmatch(ordner):
            raise PostError(f"Unzulaessiger Ordnername: {ordner}")
        path = (self.content_dir / ordner).resolve()
        if path.parent != self.content_dir.resolve():
            raise PostError("Ordner liegt ausserhalb von content/.")
        if not path.is_dir():
            raise PostError(f"Ordner fehlt: content/{ordner}/")
        return path

    def entry_path(self, section_id: str, name: str) -> Path:
        if name in RESERVED or not SAFE_NAME.fullmatch(name):
            raise PostError(f"Unzulaessiger Dateiname: {name}")
        ordner = self.section_dir(section_id)
        path = (ordner / name).resolve()
        if path.parent != ordner:
            raise PostError("Pfad liegt ausserhalb des Sektionsordners.")
        return path

    def upload_dir(self, jahr: str) -> Path:
        ordner = (self.site_dir / "static" / UPLOAD_DIR / jahr).resolve()
        if not ordner.is_relative_to((self.site_dir / "static").resolve()):
            raise PostError("Ordner liegt ausserhalb von static/.")
        return ordner

    def upload_entgegennehmen(self) -> None:
        art = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        # Ein fremdes <form> kann kein image/* schicken -- das ist der Schutz
        # davor, dass eine andere Webseite hier heimlich etwas ablegt.
        if not art.startswith("image/"):
            raise PostError("Anfrage muss ein Bild sein (Content-Type image/...).")

        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge <= 0:
            raise PostError("Leere Anfrage.")
        if laenge > UPLOAD_MAX_BYTES:
            raise PostError(f"Bild ist groesser als {UPLOAD_MAX_BYTES // 1024 // 1024} MB.")

        rohdaten = self.rfile.read(laenge)
        webp, breite, hoehe = bild_umwandeln(rohdaten)

        # Der Name kommt URL-kodiert im Header, damit auch Umlaute ankommen.
        roh_name = unquote(self.headers.get("X-Dateiname") or "bild")
        stamm = slugify(Path(roh_name).stem) or "bild"
        jahr = date.today().strftime("%Y")

        with CMSHandler.schreib_sperre:
            ordner = self.upload_dir(jahr)
            ordner.mkdir(parents=True, exist_ok=True)
            ziel = ordner / f"{stamm}.webp"
            zaehler = 2
            while ziel.exists():
                ziel = ordner / f"{stamm}-{zaehler}.webp"
                zaehler += 1
            ziel.write_bytes(webp)
            self.log_message("Bild: %s (%d KB, %dx%d)", ziel.name,
                             len(webp) // 1024, breite, hoehe)
            antwort = {
                "url": f"/{UPLOAD_DIR}/{jahr}/{ziel.name}",
                "breite": breite, "hoehe": hoehe, "bytes": len(webp),
                "build": self.bauen(),
                "git": self.sichern(f"Bild {ziel.name} hinzugefuegt", [ziel]),
            }
        self.send_json(antwort, 201)

    def upload_ausliefern(self, jahr: str, name: str) -> None:
        """Damit das Bild schon in der Vorschau erscheint, bevor Hugo es kopiert."""
        pfad = (self.upload_dir(jahr) / name).resolve()
        if pfad.parent != self.upload_dir(jahr) or not pfad.is_file():
            raise FileNotFoundError(f"Bild nicht gefunden: {jahr}/{name}")
        daten = pfad.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/webp")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def bauen(self) -> dict:
        """Seite neu erzeugen und das Ergebnis fuer die Antwort aufbereiten."""
        if self.hugo is None:
            return {"ok": True, "aus": True, "meldung": ""}
        ergebnis = hugo_bauen(self.site_dir, self.hugo)
        if not ergebnis["ok"]:
            # Vollen Text ins Protokoll, in die Oberflaeche nur die eine Zeile.
            self.log_message("Build fehlgeschlagen: %s", ergebnis.get("ausgabe", ""))
        ergebnis.pop("ausgabe", None)
        return ergebnis

    def sichern(self, nachricht: str, pfade: list[Path]) -> dict | None:
        """Commit sofort, Push nebenher. Gibt None zurueck, wenn abgeschaltet."""
        if self.repo is None:
            return None

        ergebnis = git_commit(self.repo, pfade, nachricht,
                              self.git_name, self.git_email)
        if not ergebnis["ok"]:
            self.log_error("Commit fehlgeschlagen: %s", ergebnis["meldung"])
        elif self.push:
            threading.Thread(target=self.push_nebenher, daemon=True).start()

        # Was der letzte Push gemeldet hat, faehrt hier mit.
        with CMSHandler.push_sperre:
            if CMSHandler.letzter_push is not None:
                ergebnis["push"] = CMSHandler.letzter_push
        return ergebnis

    def push_nebenher(self) -> None:
        if not CMSHandler.push_lauf.acquire(blocking=False):
            CMSHandler.push_nochmal = True   # laeuft schon, der nimmt es mit
            return
        try:
            while True:
                CMSHandler.push_nochmal = False
                ergebnis = git_push(self.repo)
                if not ergebnis["ok"]:
                    self.log_error("Push fehlgeschlagen: %s", ergebnis["meldung"])
                with CMSHandler.push_sperre:
                    # Ein gelungener Push loescht die alte Klage wieder.
                    CMSHandler.letzter_push = None if ergebnis["ok"] else ergebnis
                if not CMSHandler.push_nochmal:
                    return
        finally:
            CMSHandler.push_lauf.release()

    def free_path(self, section_id: str, slug: str) -> Path:
        candidate = self.entry_path(section_id, f"{slug}.md")
        zaehler = 2
        while candidate.exists():
            candidate = self.entry_path(section_id, f"{slug}-{zaehler}.md")
            zaehler += 1
        return candidate

    # --- Routen -------------------------------------------------------
    def do_GET(self) -> None:
        if self.path == "/api/site":
            self.guard(lambda: self.send_json({"title": self.site_title}))
            return
        if self.path == "/api/sections":
            self.guard(lambda: self.send_json(self.list_sections()))
            return
        treffer = ENTRIES_ROUTE.fullmatch(self.path)
        if treffer:
            section_id, name = treffer.groups()
            if name:
                self.guard(lambda: self.send_json(self.show_entry(section_id, name)))
            else:
                self.guard(lambda: self.send_json(self.list_entries(section_id)))
            return
        treffer = UPLOAD_ROUTE.fullmatch(self.path)
        if treffer:
            self.guard(lambda: self.upload_ausliefern(*treffer.groups()))
            return
        if self.path.startswith("/api/"):
            self.send_json({"error": "Unbekannte Route."}, 404)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/api/uploads":
            self.guard(self.upload_entgegennehmen)
            return
        treffer = ENTRIES_ROUTE.fullmatch(self.path)
        if treffer and not treffer.group(2):
            self.guard(lambda: self.create_entry(treffer.group(1)))
            return
        self.send_json({"error": "Unbekannte Route."}, 404)

    def do_PUT(self) -> None:
        treffer = ENTRIES_ROUTE.fullmatch(self.path)
        if treffer and treffer.group(2):
            self.guard(lambda: self.update_entry(treffer.group(1), treffer.group(2)))
            return
        self.send_json({"error": "Unbekannte Route."}, 404)

    def do_DELETE(self) -> None:
        treffer = ENTRIES_ROUTE.fullmatch(self.path)
        if treffer and treffer.group(2):
            self.guard(lambda: self.delete_entry(treffer.group(1), treffer.group(2)))
            return
        self.send_json({"error": "Unbekannte Route."}, 404)

    # --- Aktionen -----------------------------------------------------
    def list_sections(self) -> list[dict]:
        liste = []
        for eintrag in self.sections:
            daten = dict(eintrag)
            try:
                daten["count"] = len(self.entry_paths(eintrag["id"]))
            except PostError:
                # Ordner fehlt in Hugo: Sektion trotzdem zeigen, aber markiert.
                daten["count"] = 0
                daten["missing"] = True
            liste.append(daten)
        return liste

    def entry_paths(self, section_id: str) -> list[Path]:
        ordner = self.section_dir(section_id)
        return [p for p in sorted(ordner.glob("*.md")) if p.name not in RESERVED]

    def list_entries(self, section_id: str) -> list[dict]:
        eintraege = []
        for path in self.entry_paths(section_id):
            try:
                eintrag = read_entry(path)
                eintrag.pop("body")
            except PostError as exc:
                # Eine unlesbare Datei darf nicht die ganze Sektion lahmlegen:
                # der Rest bleibt bedienbar, diese eine wird markiert.
                self.log_message("unlesbar: %s (%s)", path.name, exc)
                eintrag = {"file": path.name, "title": path.name, "date": "",
                           "draft": False, "fehler": str(exc)}
            eintraege.append(eintrag)
        eintraege.sort(key=lambda e: (e["date"], e["file"]), reverse=True)
        return eintraege

    def show_entry(self, section_id: str, name: str) -> dict:
        path = self.entry_path(section_id, name)
        if not path.exists():
            raise FileNotFoundError(f"Eintrag nicht gefunden: {section_id}/{name}")
        return read_entry(path)

    def create_entry(self, section_id: str) -> None:
        title, datum, draft, body = payload_to_values(self.read_json())
        with CMSHandler.schreib_sperre:
            self.create_entry_gesperrt(section_id, title, datum, draft, body)

    def create_entry_gesperrt(self, section_id: str, title: str, datum: str,
                              draft: bool, body: str) -> None:
        path = self.free_path(section_id, slugify(title))
        schreibe_atomar(path, build_front_matter(title, datum, draft, [], body))
        self.log_message("angelegt: %s/%s", section_id, path.name)
        antwort = read_entry(path)
        antwort["build"] = self.bauen()
        antwort["git"] = self.sichern(f'"{title}" angelegt', [path])
        self.send_json(antwort, 201)

    def update_entry(self, section_id: str, name: str) -> None:
        title, datum, draft, body = payload_to_values(self.read_json())
        with CMSHandler.schreib_sperre:
            self.update_entry_gesperrt(section_id, name, title, datum, draft, body)

    def update_entry_gesperrt(self, section_id: str, name: str, title: str,
                              datum: str, draft: bool, body: str) -> None:
        path = self.entry_path(section_id, name)
        if not path.exists():
            raise FileNotFoundError(f"Eintrag nicht gefunden: {section_id}/{name}")
        _fields, extra, _body = parse_front_matter(path.read_text(encoding="utf-8"))
        schreibe_atomar(path, build_front_matter(title, datum, draft, extra, body))
        self.log_message("gespeichert: %s/%s", section_id, path.name)
        antwort = read_entry(path)
        antwort["build"] = self.bauen()
        antwort["git"] = self.sichern(f'"{title}" bearbeitet', [path])
        self.send_json(antwort)

    def delete_entry(self, section_id: str, name: str) -> None:
        with CMSHandler.schreib_sperre:
            self.delete_entry_gesperrt(section_id, name)

    def delete_entry_gesperrt(self, section_id: str, name: str) -> None:
        # entry_path haelt _index.md und Pfade ausserhalb der Sektion fern.
        path = self.entry_path(section_id, name)
        if not path.exists():
            raise FileNotFoundError(f"Eintrag nicht gefunden: {section_id}/{name}")
        path.unlink()
        self.log_message("geloescht: %s/%s", section_id, path.name)
        self.send_json({
            "file": path.name, "deleted": True,
            "build": self.bauen(),
            "git": self.sichern(f"{path.name} geloescht", [path]),
        })

    def guard(self, aktion) -> None:
        """Fuehrt eine Route aus und uebersetzt Fehler in JSON-Antworten."""
        try:
            aktion()
        except PostError as exc:
            self.send_json({"error": str(exc)}, 400)
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, 404)
        except OSError as exc:
            self.send_json({"error": f"Dateifehler: {exc}"}, 500)
        except Exception:
            # Letztes Netz: ohne das bricht die Verbindung ohne Antwort ab,
            # und die Oberflaeche zeigt nur "Failed to fetch".
            self.log_error("Unerwarteter Fehler:\n%s", traceback.format_exc())
            self.send_json({"error": "Unerwarteter Fehler im Server. "
                                     "Einzelheiten stehen im Protokoll."}, 500)


def main() -> None:
    parser = argparse.ArgumentParser(description="CMS fuer die Hugo-Inhalte")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--content", type=Path, default=DEFAULT_CONTENT_DIR,
                        help="content-Ordner der Hugo-Seite")
    parser.add_argument("--config", type=Path, default=None,
                        help=f"{CONFIG_NAME} der Seite (Vorgabe: neben content/)")
    parser.add_argument("--hugo", default="hugo",
                        help="Hugo-Programm fuer den Build nach dem Speichern")
    parser.add_argument("--kein-build", action="store_true",
                        help="nach dem Speichern nicht bauen (z. B. wenn "
                             "'hugo server' schon laeuft)")
    parser.add_argument("--git", action="store_true",
                        help="jede Aenderung als Commit festhalten")
    parser.add_argument("--push", action="store_true",
                        help="nach dem Commit nebenher pushen (braucht --git "
                             "und einen eingerichteten remote)")
    parser.add_argument("--git-name", default="CMS",
                        help="Name im Commit (Vorgabe: CMS)")
    parser.add_argument("--git-email", default="cms@localhost",
                        help="E-Mail im Commit (Vorgabe: cms@localhost)")
    args = parser.parse_args()

    content_dir = args.content.resolve()
    if not content_dir.is_dir():
        raise SystemExit(f"content-Ordner nicht gefunden: {content_dir}")

    config_path = (args.config or content_dir.parent / CONFIG_NAME).resolve()
    CMSHandler.site_title, CMSHandler.sections = load_config(config_path)
    CMSHandler.content_dir = content_dir
    CMSHandler.site_dir = content_dir.parent

    # Lieber beim Start meckern als beim ersten Speichern des Kunden.
    if args.kein_build:
        CMSHandler.hugo = None
    else:
        gefunden = shutil.which(args.hugo)
        if gefunden is None:
            raise SystemExit(
                f"Hugo nicht gefunden: {args.hugo}\n"
                f"  Pfad angeben mit --hugo, oder den Build mit --kein-build abschalten."
            )
        CMSHandler.hugo = gefunden

    if args.push and not args.git:
        raise SystemExit("--push braucht --git.")
    if args.git:
        repo = git_repo(content_dir)
        if repo is None:
            raise SystemExit(
                f"Kein Git-Repo ueber {content_dir}.\n"
                f"  Mit 'git init' eines anlegen oder --git weglassen."
            )
        CMSHandler.repo = repo
        CMSHandler.git_name = args.git_name
        CMSHandler.git_email = args.git_email
        CMSHandler.push = args.push

    server = ThreadingHTTPServer((args.host, args.port), CMSHandler)
    server.daemon_threads = True   # beim Beenden nicht auf offene Verbindungen warten
    print(f"CMS laeuft auf http://{args.host}:{args.port}")
    print(f"Seite:  {CMSHandler.site_title}")
    print(f"Konfig: {config_path}")
    print(f"Inhalte: {content_dir}")
    print(f"Build:  {CMSHandler.hugo or 'aus (--kein-build)'}")
    if CMSHandler.repo is None:
        print("Git:    aus")
    else:
        print(f"Git:    {CMSHandler.repo}"
              f"{'  (mit Push)' if CMSHandler.push else '  (nur lokal)'}")
    for eintrag in CMSHandler.sections:
        zeichen = "" if (content_dir / eintrag["dir"]).is_dir() else "  (Ordner fehlt!)"
        print(f"  {eintrag['name']} -> {eintrag['dir']}/{zeichen}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()

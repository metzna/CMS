#!/usr/bin/env python3
"""Kleines CMS fuer die Hugo-Seite.

Bearbeitet die Markdown-Dateien in den Unterordnern von website/content/.
Welche Ordner das sind, steht in SECTIONS -- eine Zeile pro Sektion.

Start:  python3 cms/server.py
        python3 cms/server.py --port 8000 --content ../website/content
"""

import argparse
import json
import re
import unicodedata
from datetime import date
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_CONTENT_DIR = BASE_DIR.parent / "website" / "content"

# Die bearbeitbaren Sektionen. "dir" ist ein Unterordner von website/content/.
# Neue Sektion: hier eine Zeile ergaenzen und den Ordner in Hugo anlegen.
SECTIONS = [
    {"id": "posts", "name": "Blog", "dir": "posts"},
    {"id": "veranstaltungen", "name": "Veranstaltungen", "dir": "veranstaltungen"},
]

# Nur einfache Dateinamen, kein Pfadwechsel, kein Listenindex des Abschnitts.
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\.md$")
SAFE_DIR = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RESERVED = {"_index.md", "index.md"}

# /api/sections/<id>/entries[/<datei>]
ENTRIES_ROUTE = re.compile(r"^/api/sections/([a-z0-9][a-z0-9_-]*)/entries(?:/([^/]+))?$")

UMLAUTE = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


class PostError(Exception):
    """Fehler, der als 400 an die Oberflaeche zurueckgeht."""


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


def read_entry(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    fields, _extra, body = parse_front_matter(raw)
    return {
        "file": path.name,
        "title": fields.get("title") or path.stem.replace("-", " ").title(),
        "date": fields.get("date") or "",
        "draft": bool(fields.get("draft")),
        "body": body,
    }


def payload_to_values(data: dict) -> tuple[str, str, bool, str]:
    title = str(data.get("title", "")).strip()
    if not title:
        raise PostError("Titel fehlt.")
    datum = str(data.get("date", "")).strip() or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", datum):
        raise PostError("Datum muss im Format JJJJ-MM-TT stehen.")
    return title, datum, bool(data.get("draft")), str(data.get("body", ""))


class CMSHandler(SimpleHTTPRequestHandler):
    content_dir: Path = DEFAULT_CONTENT_DIR

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
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            raise PostError("Leere Anfrage.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PostError(f"Ungueltiges JSON: {exc}") from exc

    def section(self, section_id: str) -> dict:
        for eintrag in SECTIONS:
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

    def free_path(self, section_id: str, slug: str) -> Path:
        candidate = self.entry_path(section_id, f"{slug}.md")
        zaehler = 2
        while candidate.exists():
            candidate = self.entry_path(section_id, f"{slug}-{zaehler}.md")
            zaehler += 1
        return candidate

    # --- Routen -------------------------------------------------------
    def do_GET(self) -> None:
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
        if self.path.startswith("/api/"):
            self.send_json({"error": "Unbekannte Route."}, 404)
            return
        super().do_GET()

    def do_POST(self) -> None:
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

    # --- Aktionen -----------------------------------------------------
    def list_sections(self) -> list[dict]:
        liste = []
        for eintrag in SECTIONS:
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
            eintrag = read_entry(path)
            eintrag.pop("body")
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
        path = self.free_path(section_id, slugify(title))
        path.write_text(build_front_matter(title, datum, draft, [], body), encoding="utf-8")
        self.log_message("angelegt: %s/%s", section_id, path.name)
        self.send_json(read_entry(path), 201)

    def update_entry(self, section_id: str, name: str) -> None:
        path = self.entry_path(section_id, name)
        if not path.exists():
            raise FileNotFoundError(f"Eintrag nicht gefunden: {section_id}/{name}")
        title, datum, draft, body = payload_to_values(self.read_json())
        _fields, extra, _body = parse_front_matter(path.read_text(encoding="utf-8"))
        path.write_text(build_front_matter(title, datum, draft, extra, body), encoding="utf-8")
        self.log_message("gespeichert: %s/%s", section_id, path.name)
        self.send_json(read_entry(path))

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


def main() -> None:
    parser = argparse.ArgumentParser(description="CMS fuer die Hugo-Inhalte")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--content", type=Path, default=DEFAULT_CONTENT_DIR,
                        help="content-Ordner der Hugo-Seite")
    args = parser.parse_args()

    content_dir = args.content.resolve()
    if not content_dir.is_dir():
        raise SystemExit(f"content-Ordner nicht gefunden: {content_dir}")

    CMSHandler.content_dir = content_dir
    server = HTTPServer((args.host, args.port), CMSHandler)
    print(f"CMS laeuft auf http://{args.host}:{args.port}")
    print(f"Inhalte: {content_dir}")
    for eintrag in SECTIONS:
        zeichen = "" if (content_dir / eintrag["dir"]).is_dir() else "  (Ordner fehlt!)"
        print(f"  {eintrag['name']} -> {eintrag['dir']}/{zeichen}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Anmeldung fuer eine CMS-Instanz.

Ein Dienst je Website, mit genau einem Zugang, gedacht hinter einem
Reverse Proxy:

  GET  /login     zeigt das Formular
  POST /login     prueft und setzt das Sitzungs-Cookie
  GET  /abmelden  loescht es
  GET  /pruefen   beantwortet dem Proxy pro Request ja oder nein

Zu welcher Seite dieser Dienst gehoert, steht in der Konfiguration. Der Host
jeder Anfrage muss dazu passen -- der Proxy reicht ihn als X-Forwarded-Host
durch. Das Cookie traegt denselben Host in sich und gilt ohne Domain= nur
fuer ihn; eine fremde Sitzung kommt hier also nicht durch.

Das CMS selbst weiss von alldem nichts. Es lauscht auf 127.0.0.1 und ist nur
ueber den Proxy erreichbar.

Start:      python3 cms/auth.py --config /etc/cms/auth.toml
Passwort:   python3 cms/auth.py --passwort
"""

import argparse
import base64
import hashlib
import hmac
import html
import os
import secrets
import time
import tomllib
from getpass import getpass
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

COOKIE = "cms_sitzung"
GUELTIG_SEKUNDEN = 12 * 3600      # Sitzung laeuft ab, auch wenn der Browser offen bleibt
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

# Bremse gegen Durchprobieren: so viele Fehlversuche je Absender und Fenster.
MAX_FEHLVERSUCHE = 10
FENSTER_SEKUNDEN = 300


# --- Passwoerter ---------------------------------------------------------

def hash_passwort(passwort: str) -> str:
    salz = secrets.token_bytes(16)
    roh = hashlib.scrypt(passwort.encode("utf-8"), salt=salz, dklen=32, **SCRYPT)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT["n"], SCRYPT["r"], SCRYPT["p"],
        base64.b64encode(salz).decode(), base64.b64encode(roh).decode(),
    )


def pruefe_passwort(gespeichert: str, passwort: str) -> bool:
    try:
        art, n, r, p, salz_b64, hash_b64 = gespeichert.split("$")
        if art != "scrypt":
            return False
        roh = hashlib.scrypt(
            passwort.encode("utf-8"), salt=base64.b64decode(salz_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(hash_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(roh, base64.b64decode(hash_b64))


# --- Cookie --------------------------------------------------------------

def _b64(rohdaten: bytes) -> str:
    return base64.urlsafe_b64encode(rohdaten).decode().rstrip("=")


def _entb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def cookie_bauen(benutzer: str, host: str, schluessel: bytes) -> str:
    """Traegt Benutzer, Host und Ablauf in sich -- der Server merkt sich nichts."""
    inhalt = f"{benutzer}|{host}|{int(time.time()) + GUELTIG_SEKUNDEN}".encode("utf-8")
    signatur = hmac.new(schluessel, inhalt, hashlib.sha256).digest()
    return f"{_b64(inhalt)}.{_b64(signatur)}"


def cookie_pruefen(wert: str, host: str, schluessel: bytes) -> str | None:
    """Gibt den Benutzernamen zurueck, wenn das Cookie zu diesem Host passt."""
    try:
        teil_inhalt, teil_signatur = wert.split(".", 1)
        inhalt = _entb64(teil_inhalt)
        signatur = _entb64(teil_signatur)
    except (ValueError, TypeError):
        return None

    erwartet = hmac.new(schluessel, inhalt, hashlib.sha256).digest()
    if not hmac.compare_digest(signatur, erwartet):
        return None

    try:
        benutzer, cookie_host, ablauf = inhalt.decode("utf-8").split("|")
    except (UnicodeDecodeError, ValueError):
        return None
    if cookie_host != host or int(ablauf) < time.time():
        return None
    return benutzer


# --- Konfiguration -------------------------------------------------------

def load_config(path: Path) -> dict:
    """Liest auth.toml. Fehler brechen den Start ab, nicht den ersten Login."""
    try:
        with path.open("rb") as datei:
            daten = tomllib.load(datei)
    except FileNotFoundError:
        raise SystemExit(f"Konfiguration nicht gefunden: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: kein gueltiges TOML -- {exc}")

    host = str(daten.get("host") or "").strip().lower()
    if not host:
        raise SystemExit(f'{path}: host fehlt, z. B. host = "cms.kunde-a.de".')
    benutzer = str(daten.get("benutzer") or "").strip()
    if not benutzer:
        raise SystemExit(f"{path}: benutzer fehlt.")
    passwort = str(daten.get("passwort") or "")
    if not passwort.startswith("scrypt$"):
        raise SystemExit(
            f"{path}: passwort ist kein scrypt-Hash. Mit "
            f"'python3 cms/auth.py --passwort' erzeugen."
        )
    return {
        "host": host,
        "name": str(daten.get("name") or host),
        "benutzer": benutzer,
        "passwort": passwort,
    }


def lade_schluessel(path: Path) -> bytes:
    """Aus der Umgebung, sonst aus einer Datei, die beim ersten Start entsteht."""
    aus_umgebung = os.environ.get("CMS_AUTH_GEHEIMNIS")
    if aus_umgebung:
        return aus_umgebung.encode("utf-8")
    if path.exists():
        return path.read_bytes()
    schluessel = secrets.token_bytes(32)
    path.write_bytes(schluessel)
    path.chmod(0o600)
    print(f"Neuer Sitzungsschluessel angelegt: {path}")
    return schluessel


# --- Seite ---------------------------------------------------------------

SEITE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Anmelden – {titel}</title>
<style>
:root {{ --fg:#1a1a1a; --muted:#666; --bg:#fdfdfc; --panel:#f4f4f1;
         --accent:#2a5db0; --line:#e0e0dc; --warn:#a8442a; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --fg:#e8e8e6; --muted:#9a9a95; --bg:#181817; --panel:#222221;
           --accent:#7aa8f0; --line:#35352f; --warn:#e59a7d; }}
}}
* {{ box-sizing: border-box; }}
body {{ margin:0; min-height:100vh; display:flex; align-items:center;
        justify-content:center; padding:1.5rem; background:var(--bg);
        color:var(--fg); font:16px/1.5 system-ui, sans-serif; }}
form {{ width:100%; max-width:22rem; background:var(--panel);
        border:1px solid var(--line); border-radius:6px; padding:1.5rem; }}
h1 {{ margin:0 0 1.25rem; font-size:1.1rem; }}
label {{ display:block; margin-bottom:0.9rem; font-size:0.8rem;
         color:var(--muted); }}
input {{ width:100%; margin-top:0.2rem; padding:0.5rem; font:inherit;
         color:var(--fg); background:var(--bg); border:1px solid var(--line);
         border-radius:4px; }}
input:focus-visible {{ outline:2px solid var(--accent); outline-offset:-1px; }}
button {{ width:100%; padding:0.55rem; font:inherit; color:#fff;
          background:var(--accent); border:1px solid var(--accent);
          border-radius:4px; cursor:pointer; }}
button:hover {{ filter:brightness(1.1); }}
.fehler {{ margin:0 0 1rem; padding:0.5rem 0.7rem; font-size:0.85rem;
           color:var(--warn); border:1px solid var(--warn); border-radius:4px; }}
</style>
</head>
<body>
<form method="post" action="/login">
  <h1>{titel}</h1>
  {fehler}
  <input type="hidden" name="ziel" value="{ziel}">
  <label>Benutzer
    <input name="benutzer" autocomplete="username" autofocus required>
  </label>
  <label>Passwort
    <input name="passwort" type="password" autocomplete="current-password" required>
  </label>
  <button type="submit">Anmelden</button>
</form>
</body>
</html>
"""


def sicheres_ziel(ziel: str) -> str:
    """Nur seiteneigene Pfade -- sonst wird der Login zur Weiterleitung fuer Fremde."""
    if ziel.startswith("/") and not ziel.startswith("//"):
        return ziel
    return "/"


class AuthHandler(BaseHTTPRequestHandler):
    seite: dict = {}
    schluessel: bytes = b""
    unsicher: bool = False           # nur fuer lokale Tests ohne HTTPS
    fehlversuche: dict[str, list[float]] = {}

    server_version = "CMS-Auth"

    # --- Hilfen ---------------------------------------------------------
    def host_passt(self) -> bool:
        """Dieser Dienst gehoert zu genau einer Seite -- sonst ist etwas falsch
        verdrahtet, meist der Caddy-Block oder host in der auth.toml."""
        roh = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        angefragt = roh.split(":")[0].strip().lower()
        if angefragt == self.seite["host"]:
            return True
        self.log_message("fremder Host: %r, erwartet %r", angefragt, self.seite["host"])
        return False

    def sende(self, status: int, koerper: bytes = b"", **kopf: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(koerper)))
        self.send_header("Cache-Control", "no-store")
        for name, wert in kopf.items():
            self.send_header(name.replace("_", "-"), wert)
        self.end_headers()
        if koerper:
            self.wfile.write(koerper)

    def zeige_login(self, ziel: str, fehler: str = "") -> None:
        seite = SEITE.format(
            titel=html.escape(self.seite["name"]),
            ziel=html.escape(sicheres_ziel(ziel), quote=True),
            fehler=f'<p class="fehler">{html.escape(fehler)}</p>' if fehler else "",
        )
        self.sende(200, seite.encode("utf-8"))

    def cookie_kopf(self, wert: str, loeschen: bool = False) -> str:
        # Kein Domain= -- das Cookie gilt nur fuer genau diesen Host.
        teile = [f"{COOKIE}={wert}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if not self.unsicher:
            teile.append("Secure")
        if loeschen:
            teile.append("Max-Age=0")
        return "; ".join(teile)

    def gebremst(self) -> bool:
        """Grobe Bremse gegen Durchprobieren, je Absender."""
        jetzt = time.time()
        absender = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0]
        versuche = [t for t in self.fehlversuche.get(absender, []) if t > jetzt - FENSTER_SEKUNDEN]
        self.fehlversuche[absender] = versuche
        return len(versuche) >= MAX_FEHLVERSUCHE

    def merke_fehlversuch(self) -> None:
        absender = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0]
        self.fehlversuche.setdefault(absender, []).append(time.time())

    # --- Routen ---------------------------------------------------------
    def do_GET(self) -> None:
        if not self.host_passt():
            self.sende(404, b"Unbekannter Host.")
            return

        pfad = urlparse(self.path)
        if pfad.path == "/pruefen":
            self.pruefen()
        elif pfad.path == "/login":
            self.zeige_login(parse_qs(pfad.query).get("ziel", ["/"])[0])
        elif pfad.path == "/abmelden":
            self.sende(302, b"", Location="/login",
                       Set_Cookie=self.cookie_kopf("", loeschen=True))
        else:
            self.sende(404, b"Nicht gefunden.")

    def do_POST(self) -> None:
        if not self.host_passt():
            self.sende(404, b"Unbekannter Host.")
            return
        if urlparse(self.path).path != "/login":
            self.sende(404, b"Nicht gefunden.")
            return

        laenge = min(int(self.headers.get("Content-Length") or 0), 8192)
        felder = parse_qs(self.rfile.read(laenge).decode("utf-8", "replace"))
        name = felder.get("benutzer", [""])[0]
        passwort = felder.get("passwort", [""])[0]
        ziel = sicheres_ziel(felder.get("ziel", ["/"])[0])

        if self.gebremst():
            self.zeige_login(ziel, "Zu viele Versuche. Bitte spaeter erneut.")
            return

        # Auch bei falschem Benutzernamen rechnen, damit die Antwortzeit
        # nicht verraet, ob es den Namen ueberhaupt gibt.
        passt_name = hmac.compare_digest(name, self.seite["benutzer"])
        passt_wort = pruefe_passwort(self.seite["passwort"], passwort)
        if not (passt_name and passt_wort):
            self.merke_fehlversuch()
            self.log_message("abgelehnt: %s@%s", name, self.seite["host"])
            self.zeige_login(ziel, "Benutzer oder Passwort stimmt nicht.")
            return

        wert = cookie_bauen(self.seite["benutzer"], self.seite["host"], self.schluessel)
        self.log_message("angemeldet: %s@%s", name, self.seite["host"])
        self.sende(302, b"", Location=ziel, Set_Cookie=self.cookie_kopf(wert))

    def pruefen(self) -> None:
        """Antwort fuer forward_auth: 200 durchlassen, 302 zum Login."""
        roh = SimpleCookie(self.headers.get("Cookie", ""))
        wert = roh[COOKIE].value if COOKIE in roh else ""
        if wert and cookie_pruefen(wert, self.seite["host"], self.schluessel):
            self.sende(200, b"")
            return
        ziel = self.headers.get("X-Forwarded-Uri", "/")
        self.sende(302, b"", Location=f"/login?ziel={quote(sicheres_ziel(ziel), safe='/')}")

    def log_message(self, format: str, *args) -> None:
        super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Anmeldung fuer eine CMS-Instanz")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--config", type=Path, default=Path("/etc/cms/auth.toml"))
    parser.add_argument("--schluessel", type=Path, default=None,
                        help="Datei mit dem Sitzungsschluessel (Vorgabe: neben --config)")
    parser.add_argument("--unsicher", action="store_true",
                        help="Cookie ohne Secure setzen -- nur fuer Tests ohne HTTPS")
    parser.add_argument("--passwort", action="store_true",
                        help="Passwort-Hash fuer auth.toml erzeugen und beenden")
    args = parser.parse_args()

    if args.passwort:
        eingabe = getpass("Passwort: ")
        if eingabe != getpass("Wiederholen: "):
            raise SystemExit("Die Eingaben stimmen nicht ueberein.")
        if not eingabe:
            raise SystemExit("Leeres Passwort.")
        print(f'\npasswort = "{hash_passwort(eingabe)}"')
        return

    config_path = args.config.resolve()
    AuthHandler.seite = load_config(config_path)
    AuthHandler.schluessel = lade_schluessel(
        (args.schluessel or config_path.parent / "auth.key").resolve()
    )
    AuthHandler.unsicher = args.unsicher

    server = ThreadingHTTPServer((args.host, args.port), AuthHandler)
    print(f"Anmeldung laeuft auf http://{args.host}:{args.port}")
    print(f"  Seite:    {AuthHandler.seite['name']}")
    print(f"  Host:     {AuthHandler.seite['host']}")
    print(f"  Benutzer: {AuthHandler.seite['benutzer']}")
    if args.unsicher:
        print("  ACHTUNG: --unsicher, Cookie ohne Secure. Nicht im Netz benutzen.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Login for one CMS instance.

One service per website, with exactly one account, meant to sit behind a
reverse proxy:

  GET  /login     shows the form
  POST /login     checks credentials and sets the session cookie
  GET  /logout    deletes it
  GET  /check     answers yes or no to the proxy for each request

Which site this service belongs to is set in the config. The host of every
request must match it -- the proxy passes it on as X-Forwarded-Host. The
cookie carries the same host inside it and, without Domain=, is only valid
for that host; a foreign session therefore doesn't get through here.

The CMS itself knows nothing about any of this. It listens on 127.0.0.1 and
is only reachable through the proxy.

Start:      python3 cms/auth.py --config /etc/cms/auth.toml
Password:   python3 cms/auth.py --hash-password
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

COOKIE = "cms_session"
SESSION_SECONDS = 12 * 3600       # session expires even if the browser stays open
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}

# Brake against brute force: this many failed attempts per sender and window.
MAX_FAILURES = 10
WINDOW_SECONDS = 300


# --- Passwords -----------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **SCRYPT)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT["n"], SCRYPT["r"], SCRYPT["p"],
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def check_password(stored: str, password: str) -> bool:
    try:
        kind, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if kind != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(base64.b64decode(hash_b64)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, base64.b64decode(hash_b64))


# --- Cookie --------------------------------------------------------------

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def build_cookie(user: str, host: str, key: bytes) -> str:
    """Carries user, host and expiry inside it -- the server remembers nothing."""
    payload = f"{user}|{host}|{int(time.time()) + SESSION_SECONDS}".encode("utf-8")
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def check_cookie(value: str, host: str, key: bytes) -> str | None:
    """Returns the user name if the cookie matches this host."""
    try:
        payload_part, signature_part = value.split(".", 1)
        payload = _unb64(payload_part)
        signature = _unb64(signature_part)
    except (ValueError, TypeError):
        return None

    expected = hmac.new(key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        user, cookie_host, expiry = payload.decode("utf-8").split("|")
        expires_at = int(expiry)
    except (UnicodeDecodeError, ValueError):
        return None
    if cookie_host != host or expires_at < time.time():
        return None
    return user


# --- Configuration -------------------------------------------------------

def load_config(path: Path) -> dict:
    """Reads auth.toml. Errors abort startup, not the first login."""
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError:
        raise SystemExit(f"Config not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: not valid TOML -- {exc}")

    host = str(data.get("host") or "").strip().lower()
    if not host:
        raise SystemExit(f'{path}: host is missing, e.g. host = "cms.kunde-a.de".')
    user = str(data.get("user") or "").strip()
    if not user:
        raise SystemExit(f"{path}: user is missing.")
    password = str(data.get("password") or "")
    if not password.startswith("scrypt$"):
        raise SystemExit(
            f"{path}: password is not a scrypt hash. Generate it with "
            f"'python3 cms/auth.py --hash-password'."
        )
    return {
        "host": host,
        "name": str(data.get("name") or host),
        "user": user,
        "password": password,
    }


def load_key(path: Path) -> bytes:
    """From the environment, otherwise from a file created on first start."""
    from_env = os.environ.get("CMS_AUTH_SECRET")
    if from_env:
        return from_env.encode("utf-8")
    if path.exists():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    path.chmod(0o600)
    print(f"Created new session key: {path}")
    return key


# --- Page ----------------------------------------------------------------

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in – {title}</title>
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
.error {{ margin:0 0 1rem; padding:0.5rem 0.7rem; font-size:0.85rem;
          color:var(--warn); border:1px solid var(--warn); border-radius:4px; }}
</style>
</head>
<body>
<form method="post" action="/login">
  <h1>{title}</h1>
  {error}
  <input type="hidden" name="next" value="{next}">
  <label>Username
    <input name="username" autocomplete="username" autofocus required>
  </label>
  <label>Password
    <input name="password" type="password" autocomplete="current-password" required>
  </label>
  <button type="submit">Sign in</button>
</form>
</body>
</html>
"""


def safe_target(target: str) -> str:
    """Only paths on this site -- otherwise the login becomes a redirect for strangers.

    No "//" (foreign host), no backslash (browsers turn "/\\x" into "//x")
    and only visible ASCII characters: a line break in the target would
    otherwise write its own headers into the response.
    """
    if (target.startswith("/") and not target.startswith("//") and "\\" not in target
            and all(0x20 < ord(char) < 0x7f for char in target)):
        return target
    return "/"


class AuthHandler(BaseHTTPRequestHandler):
    site: dict = {}
    key: bytes = b""
    insecure: bool = False           # only for local tests without HTTPS
    failures: dict[str, list[float]] = {}

    server_version = "CMS-Auth"

    # --- Helpers --------------------------------------------------------
    def host_matches(self) -> bool:
        """This service belongs to exactly one site -- otherwise something is
        wired up wrong, usually the Caddy block or host in auth.toml."""
        raw = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        requested = raw.split(":")[0].strip().lower()
        if requested == self.site["host"]:
            return True
        self.log_message("foreign host: %r, expected %r", requested, self.site["host"])
        return False

    def respond(self, status: int, body: bytes = b"", **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in headers.items():
            # Second safeguard: a line break has no business here.
            if "\r" in value or "\n" in value:
                raise ValueError(f"Line break in header {name}")
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def show_login(self, target: str, error: str = "") -> None:
        page = PAGE.format(
            title=html.escape(self.site["name"]),
            next=html.escape(safe_target(target), quote=True),
            error=f'<p class="error">{html.escape(error)}</p>' if error else "",
        )
        self.respond(200, page.encode("utf-8"))

    def cookie_header(self, value: str, delete: bool = False) -> str:
        # No Domain= -- the cookie is valid for exactly this host only.
        parts = [f"{COOKIE}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
        if not self.insecure:
            parts.append("Secure")
        if delete:
            parts.append("Max-Age=0")
        return "; ".join(parts)

    def throttled(self) -> bool:
        """Rough brake against brute force, per sender."""
        now = time.time()
        sender = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0]
        attempts = [t for t in self.failures.get(sender, []) if t > now - WINDOW_SECONDS]
        self.failures[sender] = attempts
        return len(attempts) >= MAX_FAILURES

    def record_failure(self) -> None:
        sender = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0]
        self.failures.setdefault(sender, []).append(time.time())

    # --- Routes ---------------------------------------------------------
    def do_GET(self) -> None:
        if not self.host_matches():
            self.respond(404, b"Unknown host.")
            return

        url = urlparse(self.path)
        if url.path == "/check":
            self.check()
        elif url.path == "/login":
            self.show_login(parse_qs(url.query).get("next", ["/"])[0])
        elif url.path == "/logout":
            self.respond(302, b"", Location="/login",
                         Set_Cookie=self.cookie_header("", delete=True))
        else:
            self.respond(404, b"Not found.")

    def do_POST(self) -> None:
        if not self.host_matches():
            self.respond(404, b"Unknown host.")
            return
        if urlparse(self.path).path != "/login":
            self.respond(404, b"Not found.")
            return

        # Clamp to 0..8192: rfile.read(-1) would wait until the client hangs up.
        try:
            length = max(0, min(int(self.headers.get("Content-Length") or 0), 8192))
        except ValueError:
            length = 0
        form = parse_qs(self.rfile.read(length).decode("utf-8", "replace"))
        name = form.get("username", [""])[0]
        password = form.get("password", [""])[0]
        target = safe_target(form.get("next", ["/"])[0])

        if self.throttled():
            self.show_login(target, "Too many attempts. Please try again later.")
            return

        # Compute the hash even for a wrong user name, so the response time
        # doesn't reveal whether the name exists at all.
        name_ok = hmac.compare_digest(name, self.site["user"])
        password_ok = check_password(self.site["password"], password)
        if not (name_ok and password_ok):
            self.record_failure()
            self.log_message("rejected: %s@%s", name, self.site["host"])
            self.show_login(target, "Wrong username or password.")
            return

        value = build_cookie(self.site["user"], self.site["host"], self.key)
        self.log_message("signed in: %s@%s", name, self.site["host"])
        self.respond(302, b"", Location=target, Set_Cookie=self.cookie_header(value))

    def check(self) -> None:
        """Answer for forward_auth: 200 lets through, 302 goes to the login."""
        cookies = SimpleCookie(self.headers.get("Cookie", ""))
        value = cookies[COOKIE].value if COOKIE in cookies else ""
        if value and check_cookie(value, self.site["host"], self.key):
            self.respond(200, b"")
            return
        target = self.headers.get("X-Forwarded-Uri", "/")
        self.respond(302, b"", Location=f"/login?next={quote(safe_target(target), safe='/')}")

    def log_message(self, format: str, *args) -> None:
        super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Login for one CMS instance")
    parser.add_argument("--port", type=int, default=9000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--config", type=Path, default=Path("/etc/cms/auth.toml"))
    parser.add_argument("--key", type=Path, default=None,
                        help="file with the session key (default: next to --config)")
    parser.add_argument("--insecure", action="store_true",
                        help="set the cookie without Secure -- only for tests without HTTPS")
    parser.add_argument("--hash-password", action="store_true",
                        help="generate a password hash for auth.toml and exit")
    args = parser.parse_args()

    if args.hash_password:
        entered = getpass("Password: ")
        if entered != getpass("Repeat: "):
            raise SystemExit("The entries don't match.")
        if not entered:
            raise SystemExit("Empty password.")
        print(f'\npassword = "{hash_password(entered)}"')
        return

    config_path = args.config.resolve()
    AuthHandler.site = load_config(config_path)
    AuthHandler.key = load_key(
        (args.key or config_path.parent / "auth.key").resolve()
    )
    AuthHandler.insecure = args.insecure

    server = ThreadingHTTPServer((args.host, args.port), AuthHandler)
    print(f"Login running at http://{args.host}:{args.port}")
    print(f"  Site:  {AuthHandler.site['name']}")
    print(f"  Host:  {AuthHandler.site['host']}")
    print(f"  User:  {AuthHandler.site['user']}")
    if args.insecure:
        print("  WARNING: --insecure, cookie without Secure. Do not use on a network.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

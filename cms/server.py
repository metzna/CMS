#!/usr/bin/env python3
"""Small CMS for the Hugo site.

Edits the Markdown files in the subfolders of website/content/.
Which folders those are is set in cms.toml next to the content folder.

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
except ImportError:          # without Pillow everything except image upload works
    Image = None

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_CONTENT_DIR = BASE_DIR.parent / "website" / "content"

# Which sections are editable is set in cms.toml next to the content folder.
CONFIG_NAME = "cms.toml"

# Hugo runs after every write. For this site that takes ~60 ms, so it runs
# synchronously: the response can then already say whether the site is rebuilt.
BUILD_TIMEOUT = 60

# Commit runs inside the request and is purely local. Push goes over the
# network and therefore runs in the background -- it must never hold up a save.
GIT_TIMEOUT = 15
PUSH_TIMEOUT = 120

# Only plain file names: no path change, no section list index.
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*\.md$")
SAFE_DIR = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
RESERVED = {"_index.md", "index.md"}

# Images: uploaded to static/uploads/<year>/, served under /uploads/.
# Hugo copies static/ unchanged, so the image is on the site without extra work.
UPLOAD_DIR = "uploads"
UPLOAD_MAX_BYTES = 25 * 1024 * 1024
# A post is text; 2 MB is far beyond any real one but keeps memory bounded.
JSON_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MAX_EDGE = 2000       # longer edge, enough for any screen
IMAGE_QUALITY = 80          # turns 1.1 MB into roughly 200 KB
UPLOAD_ROUTE = re.compile(r"^/uploads/(\d{4})/([a-z0-9][a-z0-9._-]*\.webp)$")

# /api/sections/<id>/entries[/<file>]
ENTRIES_ROUTE = re.compile(r"^/api/sections/([a-z0-9][a-z0-9_-]*)/entries(?:/([^/]+))?$")
# Must match the section part of ENTRIES_ROUTE, otherwise the section is unreachable.
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

UMLAUT_MAP = {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}


class PostError(Exception):
    """Error that goes back to the UI as a 400."""


def load_config(path: Path) -> tuple[str, list[dict]]:
    """Reads cms.toml and validates it.

    Anything that goes wrong here aborts startup: a broken config should
    show up when the server starts, not on the first click.
    """
    try:
        with path.open("rb") as file:
            data = tomllib.load(file)
    except FileNotFoundError:
        raise SystemExit(f"Config not found: {path}")
    except tomllib.TOMLDecodeError as exc:
        raise SystemExit(f"{path}: not valid TOML -- {exc}")

    title = str(data.get("title") or "").strip() or "CMS"
    raw = data.get("sections")
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{path}: at least one [[sections]] table is required.")

    sections: list[dict] = []
    taken: set[str] = set()
    for number, entry in enumerate(raw, 1):
        where = f"{path}: [[sections]] no. {number}"
        if not isinstance(entry, dict):
            raise SystemExit(f"{where} is not a table.")
        folder = str(entry.get("dir") or "").strip()
        if not SAFE_DIR.fullmatch(folder):
            raise SystemExit(
                f"{where}: dir is missing or invalid (lowercase, no "
                f"slash): {folder!r}"
            )
        section_id = str(entry.get("id") or folder).strip()
        if not SAFE_ID.fullmatch(section_id):
            raise SystemExit(f"{where}: id is invalid: {section_id!r}")
        if section_id in taken:
            raise SystemExit(f"{where}: id appears more than once: {section_id!r}")
        taken.add(section_id)
        sections.append({
            "id": section_id,
            "name": str(entry.get("name") or folder).strip(),
            "dir": folder,
        })
    return title, sections


def slugify(title: str) -> str:
    text = title.strip().lower()
    for umlaut, replacement in UMLAUT_MAP.items():
        text = text.replace(umlaut, replacement)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "entry"


def parse_front_matter(raw: str) -> tuple[dict, list[str], str]:
    """Splits front matter and text.

    Returns: known fields, untouched remaining lines, body text.
    """
    fields: dict[str, str | bool] = {}
    extra: list[str] = []

    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return fields, extra, raw

    # The first line consisting only of --- closes the front matter.
    # Line by line, so --- in the body (e.g. in a code block) cuts nothing off.
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return fields, extra, raw

    head_lines = lines[1:end]
    body = "\n".join(lines[end + 1:]).lstrip("\n")

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


def build_front_matter(title: str, day: str, draft: bool, extra: list[str], body: str) -> str:
    lines = ["---", f'title: "{title}"', f"date: {day}"]
    if draft:
        lines.append("draft: true")
    lines.extend(extra)
    lines.append("---")
    text = "\n".join(lines) + "\n\n" + body.strip()
    return text.rstrip() + "\n"


def run_hugo(site_dir: Path, hugo: str) -> dict:
    """Rebuilds the site.

    Hugo reports errors through the exit code and writes the text to stdout
    (not stderr), with "Error:" at the start of the line. --quiet swallows
    it, so Hugo runs without it here.

    --cleanDestinationDir is needed so a deleted post also disappears from
    public/ -- otherwise its page would stay reachable for visitors.
    """
    try:
        proc = subprocess.run(
            [hugo, "--cleanDestinationDir"], cwd=site_dir,
            capture_output=True, text=True, timeout=BUILD_TIMEOUT,
        )
    except FileNotFoundError:
        return {"ok": False, "message": f"Hugo not found: {hugo}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"Hugo has not responded for {BUILD_TIMEOUT} s."}
    except OSError as exc:
        return {"ok": False, "message": f"Hugo could not be started: {exc}"}

    if proc.returncode == 0:
        return {"ok": True, "message": ""}

    output = f"{proc.stdout}\n{proc.stderr}"
    line = next((l for l in output.splitlines() if l.startswith("Error:")), "")
    # Shorten absolute paths -- the file name is enough for the customer.
    message = line.replace(f"{site_dir}/", "") or f"Hugo exited with code {proc.returncode}."
    return {"ok": False, "message": message, "output": output.strip()}


def run_git(repo: Path, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    # LC_ALL=C: otherwise git answers in the server's language, and reading
    # its messages turns into guesswork.
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
        env={**os.environ, "LC_ALL": "C"},
    )


def first_line(proc: subprocess.CompletedProcess, fallback: str) -> str:
    """The first line names the reason; the last one is usually just an afterthought."""
    for line in f"{proc.stderr}\n{proc.stdout}".splitlines():
        if line.strip():
            return line.strip()
    return fallback


def git_repo(start: Path) -> Path | None:
    """Root of the repo that contains the content -- or None."""
    try:
        proc = run_git(start, ["rev-parse", "--show-toplevel"], GIT_TIMEOUT)
    except FileNotFoundError:
        raise SystemExit("--git is set, but git is not installed.")
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(proc.stdout.strip()) if proc.returncode == 0 else None


def git_commit(repo: Path, paths: list[Path], message: str,
               name: str, email: str) -> dict:
    """Creates a commit. The identity is passed along so the repo needs none."""
    try:
        run_git(repo, ["add", "--", *(str(p) for p in paths)], GIT_TIMEOUT)
        # Nothing staged means the text was unchanged. Check that via the
        # return code rather than the message text -- that depends on the
        # language.
        staged = run_git(repo, ["diff", "--cached", "--quiet", "--",
                                *(str(p) for p in paths)], GIT_TIMEOUT)
        if staged.returncode == 0:
            return {"ok": True, "message": ""}
        proc = run_git(repo, [
            "-c", f"user.name={name}", "-c", f"user.email={email}",
            "commit", "-m", message, "--", *(str(p) for p in paths),
        ], GIT_TIMEOUT)
    except FileNotFoundError:
        return {"ok": False, "message": "git not found."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"git has not responded for {GIT_TIMEOUT} s."}
    except OSError as exc:
        return {"ok": False, "message": f"git could not be started: {exc}"}

    if proc.returncode == 0:
        return {"ok": True, "message": ""}
    return {"ok": False, "message": first_line(
        proc, f"git commit exited with code {proc.returncode}.")}


def git_push(repo: Path) -> dict:
    try:
        proc = run_git(repo, ["push"], PUSH_TIMEOUT)
    except FileNotFoundError:
        return {"ok": False, "message": "git not found."}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": f"Push got no response for {PUSH_TIMEOUT} s."}
    except OSError as exc:
        return {"ok": False, "message": f"Push could not be started: {exc}"}
    if proc.returncode == 0:
        return {"ok": True, "message": ""}
    return {"ok": False, "message": first_line(proc, "Push failed.")}


def write_atomic(path: Path, text: str) -> None:
    """Write next to the file first, then swap it in.

    os.replace is atomic on a single filesystem. Without it, a concurrent
    reader -- loading the list, for example -- could catch a half-written
    file.

    The temporary name starts with a dot and does not end in .md, so neither
    glob("*.md") nor Hugo picks it up.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def convert_image(raw: bytes) -> tuple[bytes, int, int]:
    """Turns arbitrary image data into a downscaled WebP.

    Re-encoding doubles as the check: whatever Pillow cannot open as an image
    is rejected, and anything hidden does not survive it. The EXIF data
    disappears as well -- phone photos often contain the location there.
    """
    if Image is None:
        raise PostError("Image upload requires Pillow (pip install Pillow).")
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        # Most common real case: HEIC from an iPhone, which Pillow cannot open
        # without an add-on. The rest are broken or smuggled-in files.
        raise PostError(
            "The file could not be opened as an image. It may be an "
            "unsupported format such as HEIC from newer iPhones -- "
            "in that case choose JPEG when exporting."
        ) from exc

    # Phone photos are often only upright thanks to an EXIF field.
    image = ImageOps.exif_transpose(image)
    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    image.thumbnail((IMAGE_MAX_EDGE, IMAGE_MAX_EDGE), Image.LANCZOS)

    out = io.BytesIO()
    image.save(out, "WEBP", quality=IMAGE_QUALITY, method=4)
    return out.getvalue(), image.width, image.height


def read_entry(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # Happens when a file was created outside the CMS, e.g. by an old
        # editor in Latin-1.
        raise PostError(
            f"{path.name} is not saved as UTF-8 and cannot be read."
        ) from exc
    fields, _extra, body = parse_front_matter(raw)
    return {
        "file": path.name,
        "title": fields.get("title") or path.stem.replace("-", " ").title(),
        "date": fields.get("date") or "",
        "draft": bool(fields.get("draft")),
        "body": body,
    }


# In the front matter the title is written as title: "...". There " ends the
# value early, and \ starts an escape sequence (\b would be a control character).
TITLE_FORBIDDEN = '"\\'


def check_title(title: str) -> None:
    """Title must fit into title: "...", otherwise Hugo's front matter breaks."""
    if not title:
        raise PostError("Title is missing.")
    bad = [char for char in TITLE_FORBIDDEN if char in title]
    if bad:
        hint = ' Use “ ” or » « instead.' if '"' in title else ""
        raise PostError(
            f'Title must not contain {" or ".join(bad)} — Hugo could not '
            f'read the front matter otherwise.{hint}'
        )


def payload_to_values(data: dict) -> tuple[str, str, bool, str]:
    title = str(data.get("title", "")).strip()
    check_title(title)
    day = str(data.get("date", "")).strip() or date.today().isoformat()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise PostError("Date must be in the format YYYY-MM-DD.")
    return title, day, bool(data.get("draft")), str(data.get("body", ""))


class CMSHandler(SimpleHTTPRequestHandler):
    # One process serves one site; main() sets these from cms.toml.
    content_dir: Path = DEFAULT_CONTENT_DIR
    sections: list[dict] = []
    site_title: str = "CMS"
    site_dir: Path = DEFAULT_CONTENT_DIR.parent   # hugo.toml lives here
    hugo: str | None = None                       # None = don't build
    repo: Path | None = None                      # None = no versioning
    git_name: str = "CMS"
    git_email: str = "cms@localhost"
    push: bool = False
    # The push runs in the background; its result rides along with the next
    # response so a stuck backup does not go unnoticed.
    last_push: dict | None = None
    push_result_lock = threading.Lock()

    # Writing, building and committing don't mix concurrently: two Hugo runs
    # wipe each other's public/, two git commits fight over index.lock, and
    # free_path() could hand out the same file name twice. Read requests
    # still run freely.
    write_lock = threading.Lock()
    # If a push is already running, no second one is started -- the running
    # one picks up the new commits in another round.
    push_running = threading.Lock()
    push_again = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    # --- Helpers ------------------------------------------------------
    def send_json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        # A foreign <form> can only send text/plain and form data.
        # application/json forces the browser into a CORS preflight that this
        # server never answers -- so nothing foreign gets through.
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            raise PostError("Request must be application/json.")
        length = self.content_length(JSON_MAX_BYTES, "Request")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PostError(f"Invalid JSON: {exc}") from exc

    def content_length(self, limit: int, what: str) -> int:
        """Validated body length, checked before anything is read.

        rfile.read(-1) reads until the client closes the connection, so a
        negative value would tie up the thread; an oversized one would land
        in memory in full.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise PostError("Invalid Content-Length.") from None
        if length <= 0:
            raise PostError("Empty request.")
        if length > limit:
            raise PostError(f"{what} is larger than {limit // 1024 // 1024} MB.")
        return length

    def section(self, section_id: str) -> dict:
        for entry in self.sections:
            if entry["id"] == section_id:
                return entry
        raise FileNotFoundError(f"Section not found: {section_id}")

    def section_dir(self, section_id: str) -> Path:
        folder = self.section(section_id)["dir"]
        if not SAFE_DIR.fullmatch(folder):
            raise PostError(f"Invalid folder name: {folder}")
        path = (self.content_dir / folder).resolve()
        if path.parent != self.content_dir.resolve():
            raise PostError("Folder is outside of content/.")
        if not path.is_dir():
            raise PostError(f"Folder missing: content/{folder}/")
        return path

    def entry_path(self, section_id: str, name: str) -> Path:
        if name in RESERVED or not SAFE_NAME.fullmatch(name):
            raise PostError(f"Invalid file name: {name}")
        folder = self.section_dir(section_id)
        path = (folder / name).resolve()
        if path.parent != folder:
            raise PostError("Path is outside of the section folder.")
        return path

    def upload_dir(self, year: str) -> Path:
        folder = (self.site_dir / "static" / UPLOAD_DIR / year).resolve()
        if not folder.is_relative_to((self.site_dir / "static").resolve()):
            raise PostError("Folder is outside of static/.")
        return folder

    def receive_upload(self) -> None:
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        # A foreign <form> cannot send image/* -- that is what stops another
        # website from quietly dropping something here.
        if not kind.startswith("image/"):
            raise PostError("Request must be an image (Content-Type image/...).")

        length = self.content_length(UPLOAD_MAX_BYTES, "Image")
        raw = self.rfile.read(length)
        webp, width, height = convert_image(raw)

        # The name arrives URL-encoded in the header so umlauts survive.
        raw_name = unquote(self.headers.get("X-Filename") or "image")
        stem = slugify(Path(raw_name).stem) or "image"
        year = date.today().strftime("%Y")

        with CMSHandler.write_lock:
            folder = self.upload_dir(year)
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{stem}.webp"
            counter = 2
            while target.exists():
                target = folder / f"{stem}-{counter}.webp"
                counter += 1
            target.write_bytes(webp)
            self.log_message("image: %s (%d KB, %dx%d)", target.name,
                             len(webp) // 1024, width, height)
            response = {
                "url": f"/{UPLOAD_DIR}/{year}/{target.name}",
                "width": width, "height": height, "bytes": len(webp),
                "build": self.build(),
                "git": self.save_to_git(f"Add image {target.name}", [target]),
            }
        self.send_json(response, 201)

    def serve_upload(self, year: str, name: str) -> None:
        """So the image already shows in the preview before Hugo copies it."""
        path = (self.upload_dir(year) / name).resolve()
        if path.parent != self.upload_dir(year) or not path.is_file():
            raise FileNotFoundError(f"Image not found: {year}/{name}")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/webp")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def build(self) -> dict:
        """Rebuild the site and prepare the result for the response."""
        if self.hugo is None:
            return {"ok": True, "off": True, "message": ""}
        result = run_hugo(self.site_dir, self.hugo)
        if not result["ok"]:
            # Full text to the log, only the one line to the UI.
            self.log_message("build failed: %s", result.get("output", ""))
        result.pop("output", None)
        return result

    def save_to_git(self, message: str, paths: list[Path]) -> dict | None:
        """Commit right away, push in the background. Returns None if disabled."""
        if self.repo is None:
            return None

        result = git_commit(self.repo, paths, message,
                            self.git_name, self.git_email)
        if not result["ok"]:
            self.log_error("commit failed: %s", result["message"])
        elif self.push:
            threading.Thread(target=self.push_in_background, daemon=True).start()

        # Whatever the last push reported rides along here.
        with CMSHandler.push_result_lock:
            if CMSHandler.last_push is not None:
                result["push"] = CMSHandler.last_push
        return result

    def push_in_background(self) -> None:
        if not CMSHandler.push_running.acquire(blocking=False):
            CMSHandler.push_again = True   # already running, it will take this along
            return
        try:
            while True:
                CMSHandler.push_again = False
                result = git_push(self.repo)
                if not result["ok"]:
                    self.log_error("push failed: %s", result["message"])
                with CMSHandler.push_result_lock:
                    # A successful push clears the old complaint again.
                    CMSHandler.last_push = None if result["ok"] else result
                if not CMSHandler.push_again:
                    return
        finally:
            CMSHandler.push_running.release()

    def free_path(self, section_id: str, slug: str) -> Path:
        candidate = self.entry_path(section_id, f"{slug}.md")
        counter = 2
        while candidate.exists():
            candidate = self.entry_path(section_id, f"{slug}-{counter}.md")
            counter += 1
        return candidate

    # --- Routes -------------------------------------------------------
    def do_GET(self) -> None:
        if self.path == "/api/site":
            self.guard(lambda: self.send_json({"title": self.site_title}))
            return
        if self.path == "/api/sections":
            self.guard(lambda: self.send_json(self.list_sections()))
            return
        match = ENTRIES_ROUTE.fullmatch(self.path)
        if match:
            section_id, name = match.groups()
            if name:
                self.guard(lambda: self.send_json(self.show_entry(section_id, name)))
            else:
                self.guard(lambda: self.send_json(self.list_entries(section_id)))
            return
        match = UPLOAD_ROUTE.fullmatch(self.path)
        if match:
            self.guard(lambda: self.serve_upload(*match.groups()))
            return
        if self.path.startswith("/api/"):
            self.send_json({"error": "Unknown route."}, 404)
            return
        super().do_GET()

    def do_POST(self) -> None:
        if self.path == "/api/uploads":
            self.guard(self.receive_upload)
            return
        match = ENTRIES_ROUTE.fullmatch(self.path)
        if match and not match.group(2):
            self.guard(lambda: self.create_entry(match.group(1)))
            return
        self.send_json({"error": "Unknown route."}, 404)

    def do_PUT(self) -> None:
        match = ENTRIES_ROUTE.fullmatch(self.path)
        if match and match.group(2):
            self.guard(lambda: self.update_entry(match.group(1), match.group(2)))
            return
        self.send_json({"error": "Unknown route."}, 404)

    def do_DELETE(self) -> None:
        match = ENTRIES_ROUTE.fullmatch(self.path)
        if match and match.group(2):
            self.guard(lambda: self.delete_entry(match.group(1), match.group(2)))
            return
        self.send_json({"error": "Unknown route."}, 404)

    # --- Actions ------------------------------------------------------
    def list_sections(self) -> list[dict]:
        result = []
        for entry in self.sections:
            data = dict(entry)
            try:
                data["count"] = len(self.entry_paths(entry["id"]))
            except PostError:
                # Folder missing in Hugo: still show the section, but flagged.
                data["count"] = 0
                data["missing"] = True
            result.append(data)
        return result

    def entry_paths(self, section_id: str) -> list[Path]:
        folder = self.section_dir(section_id)
        return [p for p in sorted(folder.glob("*.md")) if p.name not in RESERVED]

    def list_entries(self, section_id: str) -> list[dict]:
        entries = []
        for path in self.entry_paths(section_id):
            try:
                entry = read_entry(path)
                entry.pop("body")
            except PostError as exc:
                # One unreadable file must not take down the whole section:
                # the rest stays usable, this one gets flagged.
                self.log_message("unreadable: %s (%s)", path.name, exc)
                entry = {"file": path.name, "title": path.name, "date": "",
                         "draft": False, "error": str(exc)}
            entries.append(entry)
        entries.sort(key=lambda e: (e["date"], e["file"]), reverse=True)
        return entries

    def show_entry(self, section_id: str, name: str) -> dict:
        path = self.entry_path(section_id, name)
        if not path.exists():
            raise FileNotFoundError(f"Entry not found: {section_id}/{name}")
        return read_entry(path)

    def create_entry(self, section_id: str) -> None:
        title, day, draft, body = payload_to_values(self.read_json())
        with CMSHandler.write_lock:
            self.create_entry_locked(section_id, title, day, draft, body)

    def create_entry_locked(self, section_id: str, title: str, day: str,
                            draft: bool, body: str) -> None:
        path = self.free_path(section_id, slugify(title))
        write_atomic(path, build_front_matter(title, day, draft, [], body))
        self.log_message("created: %s/%s", section_id, path.name)
        response = read_entry(path)
        response["build"] = self.build()
        response["git"] = self.save_to_git(f'Create "{title}"', [path])
        self.send_json(response, 201)

    def update_entry(self, section_id: str, name: str) -> None:
        title, day, draft, body = payload_to_values(self.read_json())
        with CMSHandler.write_lock:
            self.update_entry_locked(section_id, name, title, day, draft, body)

    def update_entry_locked(self, section_id: str, name: str, title: str,
                            day: str, draft: bool, body: str) -> None:
        path = self.entry_path(section_id, name)
        if not path.exists():
            raise FileNotFoundError(f"Entry not found: {section_id}/{name}")
        _fields, extra, _body = parse_front_matter(path.read_text(encoding="utf-8"))
        write_atomic(path, build_front_matter(title, day, draft, extra, body))
        self.log_message("saved: %s/%s", section_id, path.name)
        response = read_entry(path)
        response["build"] = self.build()
        response["git"] = self.save_to_git(f'Edit "{title}"', [path])
        self.send_json(response)

    def delete_entry(self, section_id: str, name: str) -> None:
        with CMSHandler.write_lock:
            self.delete_entry_locked(section_id, name)

    def delete_entry_locked(self, section_id: str, name: str) -> None:
        # entry_path keeps _index.md and paths outside the section away.
        path = self.entry_path(section_id, name)
        if not path.exists():
            raise FileNotFoundError(f"Entry not found: {section_id}/{name}")
        path.unlink()
        self.log_message("deleted: %s/%s", section_id, path.name)
        self.send_json({
            "file": path.name, "deleted": True,
            "build": self.build(),
            "git": self.save_to_git(f"Delete {path.name}", [path]),
        })

    def guard(self, action) -> None:
        """Runs a route and translates errors into JSON responses."""
        try:
            action()
        except PostError as exc:
            self.send_json({"error": str(exc)}, 400)
        except FileNotFoundError as exc:
            self.send_json({"error": str(exc)}, 404)
        except OSError as exc:
            self.send_json({"error": f"File error: {exc}"}, 500)
        except Exception:
            # Last safety net: without it the connection drops without a
            # response, and the UI only shows "Failed to fetch".
            self.log_error("unexpected error:\n%s", traceback.format_exc())
            self.send_json({"error": "Unexpected server error. "
                                     "Details are in the log."}, 500)


def main() -> None:
    parser = argparse.ArgumentParser(description="CMS for the Hugo content")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--content", type=Path, default=DEFAULT_CONTENT_DIR,
                        help="content folder of the Hugo site")
    parser.add_argument("--config", type=Path, default=None,
                        help=f"{CONFIG_NAME} of the site (default: next to content/)")
    parser.add_argument("--hugo", default="hugo",
                        help="Hugo binary for the build after saving")
    parser.add_argument("--no-build", action="store_true",
                        help="don't build after saving (e.g. when "
                             "'hugo server' is already running)")
    parser.add_argument("--git", action="store_true",
                        help="record every change as a commit")
    parser.add_argument("--push", action="store_true",
                        help="push in the background after each commit (requires "
                             "--git and a configured remote)")
    parser.add_argument("--git-name", default="CMS",
                        help="name in the commit (default: CMS)")
    parser.add_argument("--git-email", default="cms@localhost",
                        help="email in the commit (default: cms@localhost)")
    args = parser.parse_args()

    content_dir = args.content.resolve()
    if not content_dir.is_dir():
        raise SystemExit(f"content folder not found: {content_dir}")

    config_path = (args.config or content_dir.parent / CONFIG_NAME).resolve()
    CMSHandler.site_title, CMSHandler.sections = load_config(config_path)
    CMSHandler.content_dir = content_dir
    CMSHandler.site_dir = content_dir.parent

    # Better to complain at startup than on the customer's first save.
    if args.no_build:
        CMSHandler.hugo = None
    else:
        found = shutil.which(args.hugo)
        if found is None:
            raise SystemExit(
                f"Hugo not found: {args.hugo}\n"
                f"  Give the path with --hugo, or turn off the build with --no-build."
            )
        CMSHandler.hugo = found

    if args.push and not args.git:
        raise SystemExit("--push requires --git.")
    if args.git:
        repo = git_repo(content_dir)
        if repo is None:
            raise SystemExit(
                f"No git repo above {content_dir}.\n"
                f"  Create one with 'git init' or leave out --git."
            )
        CMSHandler.repo = repo
        CMSHandler.git_name = args.git_name
        CMSHandler.git_email = args.git_email
        CMSHandler.push = args.push

    server = ThreadingHTTPServer((args.host, args.port), CMSHandler)
    server.daemon_threads = True   # don't wait for open connections on shutdown
    print(f"CMS running at http://{args.host}:{args.port}")
    print(f"Site:    {CMSHandler.site_title}")
    print(f"Config:  {config_path}")
    print(f"Content: {content_dir}")
    print(f"Build:   {CMSHandler.hugo or 'off (--no-build)'}")
    if CMSHandler.repo is None:
        print("Git:     off")
    else:
        print(f"Git:     {CMSHandler.repo}"
              f"{'  (with push)' if CMSHandler.push else '  (local only)'}")
    for entry in CMSHandler.sections:
        flag = "" if (content_dir / entry["dir"]).is_dir() else "  (folder missing!)"
        print(f"  {entry['name']} -> {entry['dir']}/{flag}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()

"""notes_markdown_render domain logic shared by the Python oracle references (Flask, FastAPI, Django adapters).

Each operation returns ``(status, payload)``: a JSON-able dict, ``None`` (204) or an ``Html`` string
(``text/html``). Errors are ``{"detail": "<message>"}``. The Markdown renderer escapes the whole body first
and only then adds the supported markup (headings, emphasis, lists, links, images, block quotes, inline code,
fenced code blocks), so stored HTML is never served raw; link and image targets must be http(s), mailto or
relative. ``seq`` orders the list by the latest change.
"""

import datetime as dt
import hashlib
import hmac
import html
import os
import re
import secrets
import sqlite3
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "app.db"
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()

with sqlite3.connect(DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
          body TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
          seq INTEGER NOT NULL DEFAULT 0);
        """,
    )


class Html(str):
    """A rendered ``text/html`` fragment."""


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_pw(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    return salt + "$" + hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000).hex()


def sign(uid: int) -> str:
    return f"{uid}.{hmac.new(SECRET, str(uid).encode(), 'sha256').hexdigest()}"


def user_from_header(header: str | None) -> int | None:
    if not header or not header.startswith("Bearer "):
        return None
    uid, _, sig = header[7:].partition(".")
    if not uid.isdigit() or not hmac.compare_digest(sign(int(uid)), f"{uid}.{sig}"):
        return None
    with conn() as c:
        return int(uid) if c.execute("SELECT 1 FROM users WHERE id = ?", (int(uid),)).fetchone() else None


def _err(status: int, message: str):
    return status, {"detail": message}


# Markdown ----------------------------------------------------------------------------------------------------

_SAFE_URL = re.compile(r"^(https?://|mailto:|/|#|\./|\.\./|[^:/?#]+(?:[/?#]|$))", re.IGNORECASE)
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_STRONG = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_EM = re.compile(r"\*(.+?)\*|_(.+?)_")


def _url(raw: str) -> str | None:
    target = html.unescape(raw)
    return html.escape(target, quote=True) if _SAFE_URL.match(target) else None


def _inline(text: str) -> str:
    """Inline markup of one escaped line; code spans are left untouched (an unpaired backtick stays literal)."""
    parts = text.split("`")
    if len(parts) % 2 == 0:
        parts[-2:] = [parts[-2] + "`" + parts[-1]]
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(f"<code>{part}</code>")
            continue
        stash: list[str] = []

        def keep(markup: str, stash: list[str] = stash) -> str:
            stash.append(markup)
            return f"\x00{len(stash) - 1}\x00"

        part = _IMAGE.sub(
            lambda m: keep(f'<img src="{u}" alt="{m.group(1)}">') if (u := _url(m.group(2))) else m.group(0),
            part,
        )
        part = _LINK.sub(lambda m: keep(f'<a href="{u}">{m.group(1)}</a>') if (u := _url(m.group(2))) else m.group(0), part)
        part = _STRONG.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", part)
        part = _EM.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", part)
        out.append(re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], part))
    return "".join(out)


def render(markdown: str) -> str:
    lines = html.escape(markdown.replace("\x00", ""), quote=True).replace("\r\n", "\n").split("\n")
    out: list[str] = []
    para: list[str] = []
    i = 0

    def flush() -> None:
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            out.append("<pre><code>" + "\n".join(code) + "</code></pre>")
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", stripped)
        if heading:
            flush()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif stripped.startswith("&gt;"):
            flush()
            quote = []
            while i < len(lines) and lines[i].strip().startswith("&gt;"):
                quote.append(lines[i].strip()[4:].strip())
                i += 1
            out.append("<blockquote><p>" + _inline(" ".join(quote)) + "</p></blockquote>")
            continue
        elif re.match(r"^([-*+]|\d+\.)\s+", stripped):
            flush()
            ordered = bool(re.match(r"^\d+\.", stripped))
            tag = "ol" if ordered else "ul"
            items = []
            pattern = r"^\d+\.\s+" if ordered else r"^[-*+]\s+"
            while i < len(lines) and re.match(pattern, lines[i].strip()):
                items.append("<li>" + _inline(re.sub(pattern, "", lines[i].strip())) + "</li>")
                i += 1
            out.append(f"<{tag}>" + "".join(items) + f"</{tag}>")
            continue
        elif not stripped:
            flush()
        else:
            para.append(stripped)
        i += 1
    flush()
    return "\n".join(out)


# auth --------------------------------------------------------------------------------------------------------


def register(body):
    if not isinstance(body, dict):
        return _err(400, "JSON object expected")
    username, password = body.get("username"), body.get("password")
    if not isinstance(username, str) or not 1 <= len(username) <= 50:
        return _err(400, "username must be 1-50 characters")
    if not isinstance(password, str) or not password:
        return _err(400, "password is required")
    try:
        with conn() as c:
            cur = c.execute("INSERT INTO users (username, pw) VALUES (?, ?)", (username, hash_pw(password)))
    except sqlite3.IntegrityError:
        return _err(409, "username taken")
    return 201, {"id": cur.lastrowid, "username": username}


def login(body):
    if not isinstance(body, dict) or not isinstance(body.get("username"), str) or not isinstance(body.get("password"), str):
        return _err(401, "invalid credentials")
    with conn() as c:
        row = c.execute("SELECT id, pw FROM users WHERE username = ?", (body["username"],)).fetchone()
    if row is None or not hmac.compare_digest(hash_pw(body["password"], row["pw"].split("$")[0]), row["pw"]):
        return _err(401, "invalid credentials")
    return 200, {"token": sign(row["id"])}


# notes -------------------------------------------------------------------------------------------------------

_NOTE = "SELECT notes.*, users.username AS author FROM notes JOIN users ON users.id = notes.user_id"


def _note_json(row, *, body: bool = True) -> dict:
    out = {"id": row["id"], "title": row["title"], "published": bool(row["published"]), "updated_at": row["updated_at"]}
    if body:
        out.update(body=row["body"], author=row["author"])
    return out


def _fields(body, *, partial: bool) -> dict:
    if not isinstance(body, dict):
        raise ValueError("JSON object expected")
    out: dict = {}
    if "title" in body or not partial:
        title = body.get("title")
        if not isinstance(title, str) or not 1 <= len(title) <= 120:
            raise ValueError("title must be 1-120 characters")
        out["title"] = title
    if "body" in body or not partial:
        text = body.get("body")
        if not isinstance(text, str) or not text.strip() or len(text) > 50000:
            raise ValueError("body must be 1-50000 characters and not blank")
        out["body"] = text
    if "published" in body:
        if body["published"] is not None and not isinstance(body["published"], bool):
            raise ValueError("published must be true or false")
        out["published"] = int(bool(body["published"]))
    return out


def _next_seq(c) -> int:
    return c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM notes").fetchone()[0]


def list_notes(uid: int):
    with conn() as c:
        rows = c.execute(_NOTE + " WHERE notes.user_id = ? ORDER BY notes.seq DESC", (uid,)).fetchall()
    return 200, {"items": [_note_json(r, body=False) for r in rows]}


def create_note(uid: int, body):
    try:
        fields = _fields(body, partial=False)
    except ValueError as exc:
        return _err(400, str(exc))
    with conn() as c:
        cur = c.execute(
            "INSERT INTO notes (user_id, title, body, published, updated_at, seq) VALUES (?, ?, ?, ?, ?, ?)",
            (uid, fields["title"], fields["body"], fields.get("published", 0), now(), _next_seq(c)),
        )
        row = c.execute(_NOTE + " WHERE notes.id = ?", (cur.lastrowid,)).fetchone()
    return 201, _note_json(row)


def _owned(c, uid: int, note_id: int):
    return c.execute(_NOTE + " WHERE notes.id = ? AND notes.user_id = ?", (note_id, uid)).fetchone()


def get_note(uid: int, note_id: int):
    with conn() as c:
        row = _owned(c, uid, note_id)
    return (200, _note_json(row)) if row else _err(404, "not found")


def update_note(uid: int, note_id: int, body):
    with conn() as c:
        if _owned(c, uid, note_id) is None:
            return _err(404, "not found")
        try:
            fields = _fields(body, partial=True)
        except ValueError as exc:
            return _err(400, str(exc))
        fields.update(updated_at=now(), seq=_next_seq(c))
        sets = ", ".join(f"{k} = ?" for k in fields)
        c.execute(f"UPDATE notes SET {sets} WHERE id = ?", [*fields.values(), note_id])  # noqa: S608
        row = _owned(c, uid, note_id)
    return 200, _note_json(row)


def delete_note(uid: int, note_id: int):
    with conn() as c:
        if _owned(c, uid, note_id) is None:
            return _err(404, "not found")
        c.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return 204, None


def render_note(uid: int, note_id: int):
    with conn() as c:
        row = _owned(c, uid, note_id)
    return (200, Html(render(row["body"]))) if row else _err(404, "not found")


def render_public(note_id: int):
    with conn() as c:
        row = c.execute(_NOTE + " WHERE notes.id = ? AND notes.published = 1", (note_id,)).fetchone()
    return (200, Html(render(row["body"]))) if row else _err(404, "not found")

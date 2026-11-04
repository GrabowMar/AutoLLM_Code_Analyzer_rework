"""team_roles_multitenant domain logic shared by the Python kit references (Flask, FastAPI, Django adapters).

Each operation returns ``(status, payload)``; ``payload`` is a JSON-able dict or ``None`` (204). Roles:
owner > admin > member > viewer. Owners and admins manage members (an admin can neither grant nor touch the owner
role); owners, admins and members write projects; owners and admins delete them; every member reads. A
non-member sees 404 for anything in the organization (no tenant leaks); the last owner cannot be removed or
demoted (409).
"""

import datetime as dt
import hashlib
import hmac
import os
import secrets
import sqlite3
from pathlib import Path

DATA = Path(os.environ.get("DATA_DIR", "data")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "app.db"
SECRET = (os.environ.get("SECRET_KEY") or secrets.token_hex(32)).encode()
ROLES = ("owner", "admin", "member", "viewer")
STATUSES = ("active", "archived")

with sqlite3.connect(DB) as _conn:
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS orgs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          role TEXT NOT NULL, UNIQUE (org_id, user_id));
        CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL,
          description TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
        """,
    )


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
    return status, {"error": message}


def _str(body: dict, key: str, max_len: int, *, required: bool) -> str | None:
    value = body.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not 1 <= len(value) <= max_len:
        raise ValueError(f"{key} must be 1-{max_len} characters")
    return value


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


# orgs and members --------------------------------------------------------------------------------------------


def _role(c, org_id: int, uid: int) -> str | None:
    row = c.execute("SELECT role FROM members WHERE org_id = ? AND user_id = ?", (org_id, uid)).fetchone()
    return row["role"] if row else None


def _member_json(row) -> dict:
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def create_org(uid: int, body):
    if not isinstance(body, dict):
        return _err(400, "JSON object expected")
    try:
        name = _str(body, "name", 100, required=True)
    except ValueError as exc:
        return _err(400, str(exc))
    with conn() as c:
        created = now()
        cur = c.execute("INSERT INTO orgs (name, created_at) VALUES (?, ?)", (name, created))
        c.execute("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, 'owner')", (cur.lastrowid, uid))
    return 201, {"id": cur.lastrowid, "name": name, "created_at": created, "role": "owner"}


def list_orgs(uid: int):
    with conn() as c:
        rows = c.execute(
            "SELECT orgs.*, members.role FROM orgs JOIN members ON members.org_id = orgs.id"
            " WHERE members.user_id = ? ORDER BY orgs.id",
            (uid,),
        ).fetchall()
    return 200, {"items": [{"id": r["id"], "name": r["name"], "created_at": r["created_at"], "role": r["role"]} for r in rows]}


def list_members(uid: int, org_id: int):
    with conn() as c:
        if _role(c, org_id, uid) is None:
            return _err(404, "not found")
        rows = c.execute(
            "SELECT members.id, users.username, members.role FROM members JOIN users ON users.id = members.user_id"
            " WHERE members.org_id = ? ORDER BY members.id",
            (org_id,),
        ).fetchall()
    return 200, {"items": [_member_json(r) for r in rows]}


def add_member(uid: int, org_id: int, body):
    with conn() as c:
        mine = _role(c, org_id, uid)
        if mine is None:
            return _err(404, "not found")
        if not isinstance(body, dict):
            return _err(400, "JSON object expected")
        try:
            username = _str(body, "username", 50, required=True)
        except ValueError as exc:
            return _err(400, str(exc))
        role = body.get("role")
        if role not in ROLES:
            return _err(400, "role must be owner, admin, member or viewer")
        if mine not in ("owner", "admin") or (role == "owner" and mine != "owner"):
            return _err(403, "your role does not allow this")
        user = c.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if user is None:
            return _err(404, "no such user")
        try:
            cur = c.execute("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, ?)", (org_id, user["id"], role))
        except sqlite3.IntegrityError:
            return _err(409, "already a member")
    return 201, {"id": cur.lastrowid, "username": username, "role": role}


def _target(c, org_id: int, username: str):
    return c.execute(
        "SELECT members.id, members.role, members.user_id, users.username FROM members"
        " JOIN users ON users.id = members.user_id WHERE members.org_id = ? AND users.username = ?",
        (org_id, username),
    ).fetchone()


def _owners(c, org_id: int) -> int:
    return c.execute("SELECT COUNT(*) FROM members WHERE org_id = ? AND role = 'owner'", (org_id,)).fetchone()[0]


def update_member(uid: int, org_id: int, username: str, body):
    with conn() as c:
        mine = _role(c, org_id, uid)
        if mine is None:
            return _err(404, "not found")
        if not isinstance(body, dict) or body.get("role") not in ROLES:
            return _err(400, "role must be owner, admin, member or viewer")
        role = body["role"]
        target = _target(c, org_id, username)
        if target is None:
            return _err(404, "not found")
        if mine not in ("owner", "admin") or (mine != "owner" and "owner" in (role, target["role"])):
            return _err(403, "your role does not allow this")
        if target["role"] == "owner" and role != "owner" and _owners(c, org_id) == 1:
            return _err(409, "an organization keeps at least one owner")
        c.execute("UPDATE members SET role = ? WHERE id = ?", (role, target["id"]))
    return 200, {"id": target["id"], "username": target["username"], "role": role}


def remove_member(uid: int, org_id: int, username: str):
    with conn() as c:
        mine = _role(c, org_id, uid)
        if mine is None:
            return _err(404, "not found")
        target = _target(c, org_id, username)
        if target is None:
            return _err(404, "not found")
        own = target["user_id"] == uid
        if not own and (mine not in ("owner", "admin") or (mine != "owner" and target["role"] == "owner")):
            return _err(403, "your role does not allow this")
        if target["role"] == "owner" and _owners(c, org_id) == 1:
            return _err(409, "an organization keeps at least one owner")
        c.execute("DELETE FROM members WHERE id = ?", (target["id"],))
    return 204, None


# projects ----------------------------------------------------------------------------------------------------


def _project_json(row) -> dict:
    return {
        "id": row["id"], "name": row["name"], "description": row["description"], "status": row["status"],
        "org_id": row["org_id"], "created_at": row["created_at"],
    }


def _project_fields(body, *, partial: bool) -> dict:
    if not isinstance(body, dict):
        raise ValueError("JSON object expected")
    out: dict = {}
    if "name" in body or not partial:
        out["name"] = _str(body, "name", 120, required=True)
    if "description" in body:
        description = body["description"]
        if description is not None and (not isinstance(description, str) or len(description) > 2000):
            raise ValueError("description must be at most 2000 characters")
        out["description"] = description
    if "status" in body:
        if body["status"] is not None and body["status"] not in STATUSES:
            raise ValueError("status must be active or archived")
        out["status"] = body["status"] or "active"
    return out


def list_projects(uid: int, org_id: int, status: str | None):
    with conn() as c:
        if _role(c, org_id, uid) is None:
            return _err(404, "not found")
        if status is not None and status not in STATUSES:
            return _err(400, "status must be active or archived")
        sql, args = "SELECT * FROM projects WHERE org_id = ?", [org_id]
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        rows = c.execute(sql + " ORDER BY id DESC", args).fetchall()
    return 200, {"items": [_project_json(r) for r in rows]}


def create_project(uid: int, org_id: int, body):
    with conn() as c:
        mine = _role(c, org_id, uid)
        if mine is None:
            return _err(404, "not found")
        try:
            fields = _project_fields(body, partial=False)
        except ValueError as exc:
            return _err(400, str(exc))
        if mine == "viewer":
            return _err(403, "viewers cannot create projects")
        cur = c.execute(
            "INSERT INTO projects (org_id, name, description, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (org_id, fields["name"], fields.get("description"), fields.get("status") or "active", now()),
        )
        row = c.execute("SELECT * FROM projects WHERE id = ?", (cur.lastrowid,)).fetchone()
    return 201, _project_json(row)


def _visible_project(c, uid: int, project_id: int):
    row = c.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        return None, None
    role = _role(c, row["org_id"], uid)
    return (row, role) if role else (None, None)


def get_project(uid: int, project_id: int):
    with conn() as c:
        row, _role_ = _visible_project(c, uid, project_id)
    return (200, _project_json(row)) if row else _err(404, "not found")


def update_project(uid: int, project_id: int, body):
    with conn() as c:
        row, role = _visible_project(c, uid, project_id)
        if row is None:
            return _err(404, "not found")
        try:
            fields = _project_fields(body, partial=True)
        except ValueError as exc:
            return _err(400, str(exc))
        if role == "viewer":
            return _err(403, "viewers cannot edit projects")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            c.execute(f"UPDATE projects SET {sets} WHERE id = ?", [*fields.values(), project_id])  # noqa: S608
        row = c.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    return 200, _project_json(row)


def delete_project(uid: int, project_id: int):
    with conn() as c:
        row, role = _visible_project(c, uid, project_id)
        if row is None:
            return _err(404, "not found")
        if role not in ("owner", "admin"):
            return _err(403, "only owners and admins delete projects")
        c.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return 204, None

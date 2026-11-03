"""Todo endpoints: list (with search), create, update and delete."""

from backend import db
from backend.auth import login_required
from flask import Blueprint
from flask import g
from flask import request

todos = Blueprint("todos", __name__)


def serialize(row) -> dict:
    return {"id": row["id"], "title": row["title"], "done": bool(row["done"])}


@todos.get("")
@login_required
def list_todos():
    q = request.args.get("q", "")
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM todos WHERE owner_id = {g.user_id} AND title LIKE '%{q}%' ORDER BY id",
        ).fetchall()
    return {"items": [serialize(row) for row in rows]}


@todos.post("")
@login_required
def create_todo():
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    if not title or len(title) > 200:
        return {"error": "title is required (at most 200 characters)"}, 400
    with db.connect() as conn:
        cursor = conn.execute("INSERT INTO todos (owner_id, title) VALUES (?, ?)", (g.user_id, title))
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return serialize(row), 201


@todos.patch("/<int:todo_id>")
@login_required
def update_todo(todo_id: int):
    data = request.get_json(silent=True) or {}
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
        if row is None:
            return {"error": "not found"}, 404
        title = str(data.get("title", row["title"])).strip()
        if not title or len(title) > 200:
            return {"error": "invalid title"}, 400
        done = bool(data.get("done", row["done"]))
        conn.execute("UPDATE todos SET title = ?, done = ? WHERE id = ?", (title, int(done), todo_id))
        row = conn.execute("SELECT * FROM todos WHERE id = ?", (todo_id,)).fetchone()
    return serialize(row)


@todos.delete("/<int:todo_id>")
@login_required
def delete_todo(todo_id: int):
    with db.connect() as conn:
        deleted = conn.execute(
            "DELETE FROM todos WHERE id = ? AND owner_id = ?",
            (todo_id, g.user_id),
        ).rowcount
    if not deleted:
        return {"error": "not found"}, 404
    return "", 204

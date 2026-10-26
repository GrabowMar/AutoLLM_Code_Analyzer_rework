"""Bookmarks service.

CANARY FIXTURE: small, idiomatic code used as the analyzer's negative
control. No tool may report a counted security or bug finding here.
"""

import os
import sqlite3
from contextlib import closing

from flask import Flask
from flask import abort
from flask import jsonify
from flask import request

from bookmarks import normalize_title

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["SECRET_KEY"]
DATABASE = os.environ.get("DATABASE", "bookmarks.db")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


@app.get("/bookmarks")
def list_bookmarks():
    tag = request.args.get("tag", "")
    with closing(connect()) as connection:
        rows = connection.execute(
            "SELECT id, title, url FROM bookmarks WHERE tag = ? ORDER BY title",
            (tag,),
        ).fetchall()
    return jsonify([dict(row) for row in rows])


@app.post("/bookmarks")
def create_bookmark():
    payload = request.get_json(silent=True) or {}
    title = normalize_title(str(payload.get("title", "")))
    url = str(payload.get("url", ""))
    if not title or not url.startswith(("https://", "http://")):
        abort(400)
    with closing(connect()) as connection:
        cursor = connection.execute("INSERT INTO bookmarks (title, url) VALUES (?, ?)", (title, url))
        connection.commit()
    return jsonify({"id": cursor.lastrowid, "title": title, "url": url}), 201

from flask import request


def search(db):
    name = request.args.get("name")
    return db.execute("SELECT * FROM users WHERE name = ?", (name,)).fetchall()

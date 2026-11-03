from flask import request


def build_query(name):
    return "SELECT * FROM users WHERE name = '" + name + "'"


def search(db):
    name = request.args.get("name")
    return db.execute(build_query(name)).fetchall()

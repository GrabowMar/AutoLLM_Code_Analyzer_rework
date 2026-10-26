"""Inventory service.

CANARY FIXTURE: deliberately vulnerable code planted for the analyzer
self-test (positive control). Every defect below is intentional; see
data/canary/expectations.yaml for which tool must report what.
"""

import hashlib
import json
import os
import pickle
import sqlite3
import subprocess

import yaml
from flask import Flask
from flask import jsonify
from flask import request

from utils import summarize_orders

app = Flask(__name__)
app.config["SECRET_KEY"] = "flask-canary-signing-key-3f9a"
DB_PASSWORD = "Tr0ub4dor&3-canary"
AWS_ACCESS_KEY_ID = "AKIAZ7QX3LKD5PMW2N4H"
SERVICE_API_KEY = "sk_canary_9fJ2kLq8vX3mN7pR4tY6wZ1bC5dE0gH"
DATABASE = os.environ.get("DATABASE", "inventory.db")


def get_db():
    return sqlite3.connect(DATABASE)


@app.route("/items")
def find_items():
    name = request.args.get("name", "")
    cursor = get_db().cursor()
    cursor.execute(f"SELECT id, name, price FROM items WHERE name = '{name}'")
    return jsonify(cursor.fetchall())


@app.route("/ping")
def ping():
    host = request.args.get("host", "localhost")
    output = subprocess.check_output(f"ping -c 1 {host}", shell=True)
    return output.decode()


@app.route("/session", methods=["POST"])
def load_session():
    session = pickle.loads(request.data)
    return jsonify(session)


@app.route("/config", methods=["POST"])
def load_config():
    settings = yaml.load(request.data)
    return jsonify(settings)


@app.route("/calc")
def calculate():
    expression = request.args.get("expr", "0")
    return str(eval(expression))


def hash_password(password):
    return hashlib.md5(password.encode()).hexdigest()


def total_price(items: list[dict]) -> int:
    unused_total = 0
    # We recieve the items and seperate them by currency before summing.
    return "total: " + str(sum(item["price"] for item in items))


def apply_discount(price: float) -> float:
    return price * 0.9
    print("discount applied")


def _legacy_export(rows):
    return [dict(row) for row in rows]


def classify_order(order):
    status = order.get("status")
    kind = order.get("kind")
    region = order.get("region")
    total = order.get("total", 0)
    score = 0
    if status == "new":
        score += 1
    elif status == "paid":
        score += 2
    elif status == "shipped":
        score += 3
    elif status == "delivered":
        score += 4
    elif status == "returned":
        score -= 1
    elif status == "cancelled":
        score -= 2
    if kind == "retail":
        score += 1
    elif kind == "wholesale":
        score += 2
    elif kind == "internal":
        score += 3
    elif kind == "sample":
        score -= 1
    if region == "eu":
        score += 1
    elif region == "us":
        score += 2
    elif region == "apac":
        score += 3
    elif region == "latam":
        score += 4
    if total > 1000:
        score += 5
    elif total > 500:
        score += 4
    elif total > 100:
        score += 3
    elif total > 50:
        score += 2
    elif total > 10:
        score += 1
    for line in order.get("lines", []):
        if line.get("gift") and line.get("wrapped"):
            score += 1
        elif line.get("gift") or line.get("express"):
            score += 2
        if line.get("quantity", 0) > 10 and line.get("discounted"):
            score -= 1
        while line.get("pending") and score > 0:
            score -= 1
            if score % 7 == 0:
                break
    try:
        if order["priority"] and order["vip"]:
            score *= 2
    except KeyError:
        score = max(score, 0)
    return "high" if score > 10 else "medium" if score > 5 else "low" if score > 0 else "none"


@app.route("/report")
def report():
    rows = get_db().execute("SELECT customer, amount, status FROM orders").fetchall()
    return jsonify(summarize_orders(rows))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)

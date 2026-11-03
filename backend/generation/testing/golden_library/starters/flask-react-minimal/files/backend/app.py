"""Flask entry point: the health check and the frontend build."""

import os
from pathlib import Path

from flask import Flask
from flask import send_from_directory

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/")
    @app.get("/<path:path>")
    def spa(path: str = "index.html"):
        if (DIST / path).is_file():
            return send_from_directory(DIST, path)
        return send_from_directory(DIST, "index.html")

    return app


if __name__ == "__main__":
    create_app().run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))

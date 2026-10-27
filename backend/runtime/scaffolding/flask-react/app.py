import os
from pathlib import Path

from flask import Flask
from flask import jsonify
from flask import send_from_directory

# LLM: Replace this file with your full application.
# Rules that must be followed:
#   1. Do NOT pass static_folder or static_url_path to Flask() — the catch-all below serves
#      the built assets from ./static by path and returns index.html for every other
#      non-API path
#   2. All API routes MUST use the /api/ prefix
#   3. Keep _serve_spa as the very last route — do not add routes after it
#   4. Database: use "sqlite:////app/data/app.db" (the absolute path of the writable data/
#      directory) — do NOT use the relative sqlite:///data/app.db form; Flask-SQLAlchemy 3.x
#      resolves relative URIs against instance_path, not cwd.

app = Flask(__name__)
_STATIC = Path(__file__).parent / "static"

# --- LLM: insert imports, config, db models, and routes here ---


@app.route("/api/health")
def health():
    return jsonify({"status": "ok"})


# --- end of generated routes ---


# SPA catch-all: serves files from ./static (also when requested with the /static/
# prefix) or falls back to index.html for client-side routes. Unknown /api/ paths
# stay a JSON 404.
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def _serve_spa(path):
    if path == "api" or path.startswith("api/"):
        return jsonify({"detail": "Not found"}), 404
    rel = path.removeprefix("static/")
    if rel and (_STATIC / rel).is_file():
        return send_from_directory(str(_STATIC), rel)
    return send_from_directory(str(_STATIC), "index.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False)

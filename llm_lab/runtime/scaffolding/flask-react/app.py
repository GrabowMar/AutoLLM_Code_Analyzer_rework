import os
from pathlib import Path
from flask import Flask, jsonify, send_from_directory

# LLM: Replace this file with your full application.
# Rules that must be followed:
#   1. Do NOT pass static_folder or static_url_path to Flask() — the catch-all below handles all static serving
#   2. All API routes MUST use the /api/ prefix
#   3. Keep _serve_spa as the very last route — do not add routes after it
#   4. Database: use f"sqlite:///{Path(__file__).parent / 'data' / 'app.db'}" — do NOT use the
#      bare sqlite:///data/app.db form; Flask-SQLAlchemy 3.x resolves relative URIs against
#      instance_path, not cwd, so the absolute form is required.

app = Flask(__name__)
_STATIC = Path(__file__).parent / 'static'

# --- LLM: insert imports, config, db models, and routes here ---

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})

# --- end of generated routes ---

# SPA catch-all: serves static assets or falls back to index.html for client-side routes
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def _serve_spa(path):
    target = _STATIC / path if path else None
    if target and target.is_file():
        return send_from_directory(str(_STATIC), path)
    return send_from_directory(str(_STATIC), 'index.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

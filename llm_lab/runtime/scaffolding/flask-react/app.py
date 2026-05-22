import os
from flask import Flask, jsonify, send_from_directory

# LLM: Replace this file with your full application.
# Rules that must be followed:
#   1. Keep static_folder='static' on Flask()
#   2. All API routes MUST use the /api/ prefix
#   3. Keep _serve_spa as the very last route — do not add routes after it

app = Flask(__name__, static_folder='static', static_url_path='')

# --- LLM: insert imports, config, db models, and routes here ---

@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'})

# --- end of generated routes ---

# SPA catch-all: serves index.html for any path not handled above
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def _serve_spa(path):
    if path and os.path.exists(os.path.join(app.static_folder, path)):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

import os

from flask import Flask
from flask import send_file

app = Flask(__name__)
UPLOADS = "/data/uploads"


@app.route("/files/<path:filename>")
def download(filename):
    return send_file(os.path.join(UPLOADS, filename))

import os

from werkzeug.utils import secure_filename

UPLOADS = "/data/uploads"


def save(upload):
    upload.save(os.path.join(UPLOADS, secure_filename(upload.filename)))


def static_path():
    return os.path.join(UPLOADS, "static", "logo.png")

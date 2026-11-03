import os

UPLOADS = "/data/uploads"


def save(upload):
    upload.save(os.path.join(UPLOADS, upload.filename))

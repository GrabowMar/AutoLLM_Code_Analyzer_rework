import os

from flask import request
from flask import send_file

UPLOADS = "/data/uploads"


def download():
    return send_file(os.path.join(UPLOADS, request.args["name"]))

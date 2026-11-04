from flask import redirect
from flask import request
from flask import url_for


def after_login():
    next_url = request.args.get("next")
    log(next_url)
    return redirect(url_for("index"))

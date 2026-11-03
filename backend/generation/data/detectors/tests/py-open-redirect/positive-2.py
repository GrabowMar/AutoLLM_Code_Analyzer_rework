from flask import redirect
from flask import request


def after_login():
    next_url = request.args.get("next") or "/"
    session_touch()
    return redirect(next_url)

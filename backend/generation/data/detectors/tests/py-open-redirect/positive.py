from flask import redirect
from flask import request


def after_login():
    return redirect(request.args.get("next", "/"))

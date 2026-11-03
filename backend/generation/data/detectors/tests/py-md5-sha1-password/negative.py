import hashlib

from werkzeug.security import generate_password_hash


def etag(body):
    return hashlib.md5(body).hexdigest()


def store(user, password):
    user.password_hash = generate_password_hash(password)

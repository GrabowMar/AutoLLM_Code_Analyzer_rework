import hashlib


def digest(pw):
    return hashlib.sha1(pw.encode("utf-8")).hexdigest()

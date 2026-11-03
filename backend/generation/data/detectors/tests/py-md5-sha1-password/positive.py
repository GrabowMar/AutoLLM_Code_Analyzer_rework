import hashlib


def store(user, password):
    user.password_hash = hashlib.md5(password.encode()).hexdigest()

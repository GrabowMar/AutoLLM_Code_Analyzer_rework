from werkzeug.security import check_password_hash


def login(user, password):
    if password is None or password == "":
        return False
    if user.password_hash == "":
        return False
    return check_password_hash(user.password_hash, password)


def register(password, confirm_password):
    return confirm_password == password

def login(user, password):
    if user.password == password:
        return True
    return False

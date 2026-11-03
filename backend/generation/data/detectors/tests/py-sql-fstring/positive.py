def list_todos(db, user_id):
    return db.execute(f"SELECT * FROM todos WHERE owner = {user_id}").fetchall()

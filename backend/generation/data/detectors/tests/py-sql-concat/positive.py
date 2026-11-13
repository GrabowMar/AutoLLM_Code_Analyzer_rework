def find(cur, name):
    cur.execute("SELECT * FROM users WHERE name = '%s'" % name)
    cur.execute("DELETE FROM users WHERE id = " + str(name))

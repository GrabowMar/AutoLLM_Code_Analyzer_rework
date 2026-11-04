async function find(db, name) {
  return db.query("SELECT * FROM users WHERE name = $1", [name]);
}

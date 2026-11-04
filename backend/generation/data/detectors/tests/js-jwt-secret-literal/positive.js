const jwt = require("jsonwebtoken");

function issue(id) {
  return jwt.sign({ id }, "supersecret", { expiresIn: "1h" });
}

const jwt = require("jsonwebtoken");

const secret = process.env.JWT_SECRET;
if (!secret) throw new Error("JWT_SECRET is required");

function issue(id) {
  return jwt.sign({ id }, secret, { expiresIn: "1h" });
}

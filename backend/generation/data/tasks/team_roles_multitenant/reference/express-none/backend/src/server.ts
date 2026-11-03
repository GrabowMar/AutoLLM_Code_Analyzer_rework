// Express reference for team_roles_multitenant (conformance kit, contract_bearer); same rules as the Python
// references' core.py: owner > admin > member > viewer, non-members see 404, the last owner stays (409).
import express from "express";
import type { NextFunction, Request, Response } from "express";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const views = path.resolve(here, "..", "views");
const port = Number(process.env.PORT ?? 8000);
const dataDir = path.resolve(process.env.DATA_DIR ?? "data");
fs.mkdirSync(dataDir, { recursive: true });
const secret = process.env.SECRET_KEY || crypto.randomBytes(32).toString("hex");
const ROLES = ["owner", "admin", "member", "viewer"];
const STATUSES = ["active", "archived"];

const db = new DatabaseSync(path.join(dataDir, "app.db"));
db.exec(`
  CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS orgs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    role TEXT NOT NULL, UNIQUE (org_id, user_id));
  CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL,
    description TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
`);

type Row = Record<string, any>;
interface AuthedRequest extends Request { uid?: number }
const now = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
const hashPw = (pw: string, salt = crypto.randomBytes(16).toString("hex")) =>
  `${salt}$${crypto.scryptSync(pw, salt, 32).toString("hex")}`;
const sign = (uid: number) => `${uid}.${crypto.createHmac("sha256", secret).update(String(uid)).digest("hex")}`;
const fail = (res: Response, status: number, error: string) => res.status(status).json({ error });
const same = (a: string, b: string) => {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && crypto.timingSafeEqual(x, y);
};
const isObject = (v: unknown): v is Record<string, any> => v !== null && typeof v === "object" && !Array.isArray(v);
const str = (v: unknown, max: number) => typeof v === "string" && v.length >= 1 && v.length <= max;
const intParam = (v: unknown) => (/^\d+$/.test(String(v)) ? Number(v) : -1);

function auth(req: AuthedRequest, res: Response, next: NextFunction) {
  const header = req.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  const uid = Number(token.split(".")[0]);
  if (!token || !Number.isInteger(uid) || !same(sign(uid), token)) return fail(res, 401, "authentication required");
  if (!db.prepare("SELECT 1 FROM users WHERE id = ?").get(uid)) return fail(res, 401, "authentication required");
  req.uid = uid;
  next();
}

const roleOf = (orgId: number, uid: number) =>
  (db.prepare("SELECT role FROM members WHERE org_id = ? AND user_id = ?").get(orgId, uid) as Row | undefined)?.role as string | undefined;
const owners = (orgId: number) => (db.prepare("SELECT COUNT(*) AS n FROM members WHERE org_id = ? AND role = 'owner'").get(orgId) as Row).n;
const target = (orgId: number, username: string) =>
  db.prepare(
    "SELECT members.id, members.role, members.user_id, users.username FROM members JOIN users ON users.id = members.user_id WHERE members.org_id = ? AND users.username = ?",
  ).get(orgId, username) as Row | undefined;
const projectJson = (r: Row) => ({
  id: r.id, name: r.name, description: r.description, status: r.status, org_id: r.org_id, created_at: r.created_at,
});

function projectFields(body: unknown, partial: boolean): Record<string, any> | string {
  if (!isObject(body)) return "JSON object expected";
  const out: Record<string, any> = {};
  if ("name" in body || !partial) {
    if (!str(body.name, 120)) return "name must be 1-120 characters";
    out.name = body.name;
  }
  if ("description" in body) {
    if (body.description != null && (typeof body.description !== "string" || body.description.length > 2000))
      return "description must be at most 2000 characters";
    out.description = body.description;
  }
  if ("status" in body) {
    if (body.status != null && !STATUSES.includes(body.status)) return "status must be active or archived";
    out.status = body.status ?? "active";
  }
  return out;
}

function visibleProject(uid: number, id: unknown): [Row | undefined, string | undefined] {
  const row = db.prepare("SELECT * FROM projects WHERE id = ?").get(intParam(id)) as Row | undefined;
  if (!row) return [undefined, undefined];
  const role = roleOf(row.org_id, uid);
  return role ? [row, role] : [undefined, undefined];
}

const app = express();
app.use(express.json());

app.get("/api/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.post("/api/auth/register", (req, res) => {
  const { username, password } = req.body ?? {};
  if (!str(username, 50)) return fail(res, 400, "username must be 1-50 characters");
  if (typeof password !== "string" || !password) return fail(res, 400, "password is required");
  try {
    const info = db.prepare("INSERT INTO users (username, pw) VALUES (?, ?)").run(username, hashPw(password));
    res.status(201).json({ id: Number(info.lastInsertRowid), username });
  } catch {
    fail(res, 409, "username taken");
  }
});

app.post("/api/auth/login", (req, res) => {
  const { username, password } = req.body ?? {};
  if (typeof username !== "string" || typeof password !== "string") return fail(res, 401, "invalid credentials");
  const row = db.prepare("SELECT id, pw FROM users WHERE username = ?").get(username) as Row | undefined;
  if (!row || !same(hashPw(password, String(row.pw).split("$")[0]), String(row.pw))) return fail(res, 401, "invalid credentials");
  res.json({ token: sign(Number(row.id)) });
});

app.post("/api/orgs", auth, (req: AuthedRequest, res) => {
  if (!isObject(req.body) || !str(req.body.name, 100)) return fail(res, 400, "name must be 1-100 characters");
  const created = now();
  const info = db.prepare("INSERT INTO orgs (name, created_at) VALUES (?, ?)").run(req.body.name, created);
  db.prepare("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, 'owner')").run(info.lastInsertRowid, req.uid!);
  res.status(201).json({ id: Number(info.lastInsertRowid), name: req.body.name, created_at: created, role: "owner" });
});

app.get("/api/orgs", auth, (req: AuthedRequest, res) => {
  const rows = db
    .prepare("SELECT orgs.*, members.role FROM orgs JOIN members ON members.org_id = orgs.id WHERE members.user_id = ? ORDER BY orgs.id")
    .all(req.uid!) as Row[];
  res.json({ items: rows.map((r) => ({ id: r.id, name: r.name, created_at: r.created_at, role: r.role })) });
});

app.get("/api/orgs/:orgId/members", auth, (req: AuthedRequest, res) => {
  const orgId = intParam(req.params.orgId);
  if (!roleOf(orgId, req.uid!)) return fail(res, 404, "not found");
  const rows = db
    .prepare("SELECT members.id, users.username, members.role FROM members JOIN users ON users.id = members.user_id WHERE members.org_id = ? ORDER BY members.id")
    .all(orgId) as Row[];
  res.json({ items: rows.map((r) => ({ id: r.id, username: r.username, role: r.role })) });
});

app.post("/api/orgs/:orgId/members", auth, (req: AuthedRequest, res) => {
  const orgId = intParam(req.params.orgId);
  const mine = roleOf(orgId, req.uid!);
  if (!mine) return fail(res, 404, "not found");
  const body = req.body;
  if (!isObject(body) || !str(body.username, 50)) return fail(res, 400, "username must be 1-50 characters");
  if (!ROLES.includes(body.role)) return fail(res, 400, "role must be owner, admin, member or viewer");
  if (!["owner", "admin"].includes(mine) || (body.role === "owner" && mine !== "owner"))
    return fail(res, 403, "your role does not allow this");
  const user = db.prepare("SELECT id FROM users WHERE username = ?").get(body.username) as Row | undefined;
  if (!user) return fail(res, 404, "no such user");
  try {
    const info = db.prepare("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, ?)").run(orgId, user.id, body.role);
    res.status(201).json({ id: Number(info.lastInsertRowid), username: body.username, role: body.role });
  } catch {
    fail(res, 409, "already a member");
  }
});

app.patch("/api/orgs/:orgId/members/:username", auth, (req: AuthedRequest, res) => {
  const orgId = intParam(req.params.orgId);
  const mine = roleOf(orgId, req.uid!);
  if (!mine) return fail(res, 404, "not found");
  const role = isObject(req.body) ? req.body.role : undefined;
  if (!ROLES.includes(role)) return fail(res, 400, "role must be owner, admin, member or viewer");
  const t = target(orgId, String(req.params.username));
  if (!t) return fail(res, 404, "not found");
  if (!["owner", "admin"].includes(mine) || (mine !== "owner" && (role === "owner" || t.role === "owner")))
    return fail(res, 403, "your role does not allow this");
  if (t.role === "owner" && role !== "owner" && owners(orgId) === 1) return fail(res, 409, "an organization keeps at least one owner");
  db.prepare("UPDATE members SET role = ? WHERE id = ?").run(role, t.id);
  res.json({ id: t.id, username: t.username, role });
});

app.delete("/api/orgs/:orgId/members/:username", auth, (req: AuthedRequest, res) => {
  const orgId = intParam(req.params.orgId);
  const mine = roleOf(orgId, req.uid!);
  if (!mine) return fail(res, 404, "not found");
  const t = target(orgId, String(req.params.username));
  if (!t) return fail(res, 404, "not found");
  const own = t.user_id === req.uid;
  if (!own && (!["owner", "admin"].includes(mine) || (mine !== "owner" && t.role === "owner")))
    return fail(res, 403, "your role does not allow this");
  if (t.role === "owner" && owners(orgId) === 1) return fail(res, 409, "an organization keeps at least one owner");
  db.prepare("DELETE FROM members WHERE id = ?").run(t.id);
  res.status(204).end();
});

app.get("/api/orgs/:orgId/projects", auth, (req: AuthedRequest, res) => {
  const orgId = intParam(req.params.orgId);
  if (!roleOf(orgId, req.uid!)) return fail(res, 404, "not found");
  const status = req.query.status;
  if (status !== undefined && !STATUSES.includes(String(status))) return fail(res, 400, "status must be active or archived");
  const rows = (status === undefined
    ? db.prepare("SELECT * FROM projects WHERE org_id = ? ORDER BY id DESC").all(orgId)
    : db.prepare("SELECT * FROM projects WHERE org_id = ? AND status = ? ORDER BY id DESC").all(orgId, String(status))) as Row[];
  res.json({ items: rows.map(projectJson) });
});

app.post("/api/orgs/:orgId/projects", auth, (req: AuthedRequest, res) => {
  const orgId = intParam(req.params.orgId);
  const mine = roleOf(orgId, req.uid!);
  if (!mine) return fail(res, 404, "not found");
  const fields = projectFields(req.body, false);
  if (typeof fields === "string") return fail(res, 400, fields);
  if (mine === "viewer") return fail(res, 403, "viewers cannot create projects");
  const info = db
    .prepare("INSERT INTO projects (org_id, name, description, status, created_at) VALUES (?, ?, ?, ?, ?)")
    .run(orgId, fields.name, fields.description ?? null, fields.status ?? "active", now());
  res.status(201).json(projectJson(db.prepare("SELECT * FROM projects WHERE id = ?").get(info.lastInsertRowid) as Row));
});

app.get("/api/projects/:id", auth, (req: AuthedRequest, res) => {
  const [row] = visibleProject(req.uid!, req.params.id);
  if (!row) return fail(res, 404, "not found");
  res.json(projectJson(row));
});

app.patch("/api/projects/:id", auth, (req: AuthedRequest, res) => {
  const [row, role] = visibleProject(req.uid!, req.params.id);
  if (!row) return fail(res, 404, "not found");
  const fields = projectFields(req.body, true);
  if (typeof fields === "string") return fail(res, 400, fields);
  if (role === "viewer") return fail(res, 403, "viewers cannot edit projects");
  const keys = Object.keys(fields);
  if (keys.length) db.prepare(`UPDATE projects SET ${keys.map((k) => `${k} = ?`).join(", ")} WHERE id = ?`).run(...keys.map((k) => fields[k]), row.id);
  res.json(projectJson(db.prepare("SELECT * FROM projects WHERE id = ?").get(row.id) as Row));
});

app.delete("/api/projects/:id", auth, (req: AuthedRequest, res) => {
  const [row, role] = visibleProject(req.uid!, req.params.id);
  if (!row) return fail(res, 404, "not found");
  if (!["owner", "admin"].includes(role!)) return fail(res, 403, "only owners and admins delete projects");
  db.prepare("DELETE FROM projects WHERE id = ?").run(row.id);
  res.status(204).end();
});

app.get("/", (_req, res) => {
  res.sendFile(path.join(views, "index.html"));
});

app.listen(port, "0.0.0.0", () => {
  console.log(`listening on ${port}`);
});

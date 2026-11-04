// Express reference for crud_todo_list (conformance kit, contract_bearer). Storage: node:sqlite (built in).
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
const PRIORITIES = ["low", "medium", "high"];

const db = new DatabaseSync(path.join(dataDir, "app.db"));
db.exec(`
  CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
    description TEXT, priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT, created_at TEXT NOT NULL);
`);

type Row = Record<string, any>;
const now = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
const hashPw = (pw: string, salt = crypto.randomBytes(16).toString("hex")) =>
  `${salt}$${crypto.scryptSync(pw, salt, 32).toString("hex")}`;
const sign = (uid: number) => `${uid}.${crypto.createHmac("sha256", secret).update(String(uid)).digest("hex")}`;
const fail = (res: Response, status: number, error: string) => res.status(status).json({ error });

function validDate(v: unknown): boolean {
  if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const d = new Date(`${v}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().startsWith(v);
}

function validate(body: any, partial: boolean): string | null {
  if (body === null || typeof body !== "object" || Array.isArray(body)) return "JSON object expected";
  if ("title" in body || !partial) {
    if (typeof body.title !== "string" || body.title.length < 1 || body.title.length > 255) return "title must be 1-255 characters";
  }
  if (body.description != null && (typeof body.description !== "string" || body.description.length > 2000))
    return "description must be at most 2000 characters";
  if (body.priority != null && !PRIORITIES.includes(body.priority)) return "priority must be low, medium or high";
  if (body.due_date != null && !validDate(body.due_date)) return "due_date must be YYYY-MM-DD";
  if (body.completed != null && typeof body.completed !== "boolean") return "completed must be a boolean";
  return null;
}

const todoJson = (r: Row) => ({
  id: r.id, title: r.title, description: r.description, priority: r.priority, due_date: r.due_date,
  completed: Boolean(r.completed), completed_at: r.completed_at, created_at: r.created_at,
});

interface AuthedRequest extends Request { uid?: number }

function auth(req: AuthedRequest, res: Response, next: NextFunction) {
  const header = req.get("authorization") ?? "";
  if (!header.startsWith("Bearer ")) return fail(res, 401, "authentication required");
  const token = header.slice(7);
  const uid = Number(token.split(".")[0]);
  const expected = Buffer.from(sign(uid));
  const given = Buffer.from(token);
  if (!Number.isInteger(uid) || expected.length !== given.length || !crypto.timingSafeEqual(expected, given))
    return fail(res, 401, "authentication required");
  if (!db.prepare("SELECT 1 FROM users WHERE id = ?").get(uid)) return fail(res, 401, "authentication required");
  req.uid = uid;
  next();
}

const app = express();
app.use(express.json());
app.use((err: any, _req: Request, res: Response, next: NextFunction) => {
  if (err?.type === "entity.parse.failed") return fail(res, 400, "invalid JSON");
  next(err);
});

app.get("/api/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.post("/api/auth/register", (req, res) => {
  const { username, password } = req.body ?? {};
  if (typeof username !== "string" || username.length < 1 || username.length > 50)
    return fail(res, 400, "username must be 1-50 characters");
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
  if (!row) return fail(res, 401, "invalid credentials");
  const check = Buffer.from(hashPw(password, String(row.pw).split("$")[0]));
  const stored = Buffer.from(String(row.pw));
  if (check.length !== stored.length || !crypto.timingSafeEqual(check, stored)) return fail(res, 401, "invalid credentials");
  res.json({ token: sign(Number(row.id)) });
});

app.get("/api/todos", auth, (req: AuthedRequest, res) => {
  const where = ["user_id = ?"];
  const args: any[] = [req.uid];
  const completed = req.query.completed;
  if (typeof completed === "string") {
    where.push("completed = ?");
    args.push(["true", "1"].includes(completed.toLowerCase()) ? 1 : 0);
  }
  const priority = req.query.priority;
  if (priority !== undefined) {
    if (typeof priority !== "string" || !PRIORITIES.includes(priority)) return fail(res, 400, "invalid priority");
    where.push("priority = ?");
    args.push(priority);
  }
  const page = Math.max(1, Number.parseInt(String(req.query.page ?? "1"), 10) || 1);
  const pageSize = Math.min(200, Math.max(1, Number.parseInt(String(req.query.page_size ?? "50"), 10) || 50));
  const clause = where.join(" AND ");
  const total = (db.prepare(`SELECT COUNT(*) AS n FROM todos WHERE ${clause}`).get(...args) as Row).n;
  const rows = db
    .prepare(`SELECT * FROM todos WHERE ${clause} ORDER BY completed ASC, id DESC LIMIT ? OFFSET ?`)
    .all(...args, pageSize, (page - 1) * pageSize) as Row[];
  const remaining = (db.prepare("SELECT COUNT(*) AS n FROM todos WHERE user_id = ? AND completed = 0").get(req.uid!) as Row).n;
  res.json({ items: rows.map(todoJson), total, page, page_size: pageSize, remaining });
});

app.post("/api/todos", auth, (req: AuthedRequest, res) => {
  const body = req.body;
  const problem = validate(body, false);
  if (problem) return fail(res, 400, problem);
  const completed = Boolean(body.completed);
  const info = db
    .prepare(
      "INSERT INTO todos (user_id, title, description, priority, due_date, completed, completed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    )
    .run(req.uid!, body.title, body.description ?? null, body.priority ?? "medium", body.due_date ?? null,
      completed ? 1 : 0, completed ? now() : null, now());
  res.status(201).json(todoJson(db.prepare("SELECT * FROM todos WHERE id = ?").get(info.lastInsertRowid) as Row));
});

const owned = (id: string, uid: number) =>
  /^\d+$/.test(id) ? (db.prepare("SELECT * FROM todos WHERE id = ? AND user_id = ?").get(Number(id), uid) as Row | undefined) : undefined;

app.get("/api/todos/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  res.json(todoJson(row));
});

app.patch("/api/todos/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  const body = req.body;
  const problem = validate(body, true);
  if (problem) return fail(res, 400, problem);
  const fields: Record<string, any> = {};
  for (const key of ["title", "description", "priority", "due_date"]) if (key in body) fields[key] = body[key];
  if ("priority" in fields && fields.priority == null) fields.priority = "medium";
  if (body.completed != null) {
    fields.completed = body.completed ? 1 : 0;
    if (body.completed && !row.completed) fields.completed_at = now();
    else if (!body.completed) fields.completed_at = null;
  }
  const keys = Object.keys(fields);
  if (keys.length) {
    db.prepare(`UPDATE todos SET ${keys.map((k) => `${k} = ?`).join(", ")} WHERE id = ?`).run(...keys.map((k) => fields[k]), row.id);
  }
  res.json(todoJson(db.prepare("SELECT * FROM todos WHERE id = ?").get(row.id) as Row));
});

app.delete("/api/todos/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  db.prepare("DELETE FROM todos WHERE id = ?").run(row.id);
  res.status(204).end();
});

app.get("/", (_req, res) => {
  res.sendFile(path.join(views, "index.html"));
});

app.listen(port, "0.0.0.0", () => {
  console.log(`listening on ${port}`);
});

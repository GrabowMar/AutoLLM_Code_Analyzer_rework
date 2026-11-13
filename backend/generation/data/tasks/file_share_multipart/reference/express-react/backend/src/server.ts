// Express reference for file_share_multipart (conformance kit, contract_bearer). No extra dependencies:
// node:sqlite for storage and the built-in WHATWG Request.formData() for multipart parsing. Uploads over 10 MiB
// are refused with 413 from Content-Length before the body is read.
import express from "express";
import type { NextFunction, Request, Response } from "express";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT ?? 8000);
const dataDir = path.resolve(process.env.DATA_DIR ?? "data");
const filesDir = path.join(dataDir, "files");
fs.mkdirSync(filesDir, { recursive: true });
const secret = process.env.SECRET_KEY || crypto.randomBytes(32).toString("hex");
const MAX_UPLOAD = 10 * 1024 * 1024;

const db = new DatabaseSync(path.join(dataDir, "app.db"));
db.exec(`
  CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, stored TEXT NOT NULL,
    filename TEXT, description TEXT, size_bytes INTEGER, content_type TEXT, uploaded_at TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL);
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

function auth(req: AuthedRequest, res: Response, next: NextFunction) {
  const header = req.get("authorization") ?? "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : "";
  const uid = Number(token.split(".")[0]);
  if (!token || !Number.isInteger(uid) || !same(sign(uid), token)) return fail(res, 401, "authentication required");
  if (!db.prepare("SELECT 1 FROM users WHERE id = ?").get(uid)) return fail(res, 401, "authentication required");
  req.uid = uid;
  next();
}

const fileJson = (r: Row) => ({
  id: r.id, description: r.description, filename: r.filename, size_bytes: r.size_bytes,
  content_type: r.content_type, uploaded_at: r.uploaded_at, download_url: `/api/files/${r.id}/download`,
});

function send(res: Response, row: Row) {
  const safe = String(row.filename ?? "file").replace(/[^A-Za-z0-9._-]/g, "_");
  res.set({
    "Content-Type": "application/octet-stream",
    "Content-Disposition": `attachment; filename="${safe}"`,
    "X-Content-Type-Options": "nosniff",
  });
  res.send(fs.readFileSync(path.join(filesDir, row.stored)));
}

const owned = (id: string, uid: number) =>
  /^\d+$/.test(id) ? (db.prepare("SELECT * FROM files WHERE id = ? AND user_id = ?").get(Number(id), uid) as Row | undefined) : undefined;

const app = express();
app.use(express.json());

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
  if (!row || !same(hashPw(password, String(row.pw).split("$")[0]), String(row.pw))) return fail(res, 401, "invalid credentials");
  res.json({ token: sign(Number(row.id)) });
});

app.post(
  "/api/files",
  (req, res, next) => {
    if (Number(req.get("content-length") ?? 0) > MAX_UPLOAD) {
      res.set("Connection", "close");
      return fail(res, 413, "upload too large");
    }
    next();
  },
  auth,
  async (req: AuthedRequest, res) => {
    let form: FormData;
    try {
      const web = new globalThis.Request(`http://local${req.originalUrl}`, {
        method: "POST",
        headers: req.headers as Record<string, string>,
        body: Readable.toWeb(req) as ReadableStream,
        duplex: "half",
      } as RequestInit);
      form = await web.formData();
    } catch {
      return fail(res, 400, "multipart form expected");
    }
    const file = form.get("file");
    if (!file || typeof file === "string") return fail(res, 400, "file is required");
    const description = form.get("description");
    if (description !== null && (typeof description !== "string" || description.length > 300))
      return fail(res, 400, "description must be at most 300 characters");
    const data = Buffer.from(await file.arrayBuffer());
    const stored = crypto.randomBytes(16).toString("hex");
    fs.writeFileSync(path.join(filesDir, stored), data);
    const info = db
      .prepare("INSERT INTO files (user_id, stored, filename, description, size_bytes, content_type, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)")
      .run(req.uid!, stored, path.basename(file.name || "file"), description ?? null, data.length, file.type || null, now());
    res.status(201).json(fileJson(db.prepare("SELECT * FROM files WHERE id = ?").get(info.lastInsertRowid) as Row));
  },
);

app.get("/api/files", auth, (req: AuthedRequest, res) => {
  const page = Math.max(1, Number.parseInt(String(req.query.page ?? "1"), 10) || 1);
  const pageSize = Math.min(100, Math.max(1, Number.parseInt(String(req.query.page_size ?? "20"), 10) || 20));
  const total = (db.prepare("SELECT COUNT(*) AS n FROM files WHERE user_id = ?").get(req.uid!) as Row).n;
  const rows = db
    .prepare("SELECT * FROM files WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?")
    .all(req.uid!, pageSize, (page - 1) * pageSize) as Row[];
  res.json({ items: rows.map(fileJson), total, page, page_size: pageSize });
});

app.get("/api/files/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  res.json(fileJson(row));
});

app.get("/api/files/:id/download", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  send(res, row);
});

app.delete("/api/files/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  db.prepare("DELETE FROM shares WHERE file_id = ?").run(row.id);
  db.prepare("DELETE FROM files WHERE id = ?").run(row.id);
  fs.rmSync(path.join(filesDir, row.stored), { force: true });
  res.status(204).end();
});

app.post("/api/files/:id/shares", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  const token = crypto.randomBytes(24).toString("base64url");
  db.prepare("INSERT INTO shares (token, file_id) VALUES (?, ?)").run(token, row.id);
  res.status(201).json({ token, url: `/api/shared/${token}` });
});

app.delete("/api/files/:id/shares/:token", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  const info = db.prepare("DELETE FROM shares WHERE token = ? AND file_id = ?").run(String(req.params.token), row.id);
  if (!info.changes) return fail(res, 404, "not found");
  res.status(204).end();
});

app.get("/api/shared/:token", (req, res) => {
  const row = db
    .prepare("SELECT files.* FROM shares JOIN files ON files.id = shares.file_id WHERE shares.token = ?")
    .get(String(req.params.token)) as Row | undefined;
  if (!row) return fail(res, 404, "not found");
  send(res, row);
});

// The React build (frontend/dist); unknown non-API GET paths get index.html (client routing).
const dist = path.resolve(here, "..", "..", "frontend", "dist");
app.use(express.static(dist));
app.use((req, res, next) => {
  if (req.method !== "GET" || req.path.startsWith("/api/")) {
    next();
    return;
  }
  res.sendFile(path.join(dist, "index.html"));
});

app.listen(port, "0.0.0.0", () => {
  console.log(`listening on ${port}`);
});

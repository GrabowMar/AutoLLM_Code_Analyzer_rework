// Express reference for notes_markdown_render (oracle reference, contract_bearer). Storage: node:sqlite (built in).
// Errors are {"detail": "<message>"}. The Markdown renderer escapes the whole body first and only then adds the
// supported markup (headings, emphasis, lists, links, images, block quotes, inline code, fenced code blocks), so
// stored HTML is never served raw; link and image targets must be http(s), mailto or relative. `seq` orders the
// list by the latest change.
import express from "express";
import type { NextFunction, Request, Response } from "express";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const port = Number(process.env.PORT ?? 8000);
const dataDir = path.resolve(process.env.DATA_DIR ?? "data");
fs.mkdirSync(dataDir, { recursive: true });
const secret = process.env.SECRET_KEY || crypto.randomBytes(32).toString("hex");

const db = new DatabaseSync(path.join(dataDir, "app.db"));
db.exec(`
  CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
    body TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0);
`);

type Row = Record<string, any>;
const now = () => new Date().toISOString().replace(/\.\d+Z$/, "Z");
const hashPw = (pw: string, salt = crypto.randomBytes(16).toString("hex")) =>
  `${salt}$${crypto.scryptSync(pw, salt, 32).toString("hex")}`;
const sign = (uid: number) => `${uid}.${crypto.createHmac("sha256", secret).update(String(uid)).digest("hex")}`;
const fail = (res: Response, status: number, detail: string) => res.status(status).json({ detail });

// Markdown ------------------------------------------------------------------------------------------------------

const escapeHtml = (s: string) =>
  s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
const unescapeHtml = (s: string) =>
  s.replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#x27;/g, "'").replace(/&amp;/g, "&");
const SAFE_URL = /^(https?:\/\/|mailto:|\/|#|\.\/|\.\.\/|[^:/?#]+(?:[/?#]|$))/i;

function safeUrl(raw: string): string | null {
  const target = unescapeHtml(raw);
  return SAFE_URL.test(target) ? escapeHtml(target) : null;
}

function inline(text: string): string {
  const parts = text.split("`");
  if (parts.length % 2 === 0) parts.splice(parts.length - 2, 2, `${parts[parts.length - 2]}\`${parts[parts.length - 1]}`);
  return parts
    .map((part, i) => {
      if (i % 2) return `<code>${part}</code>`;
      const stash: string[] = [];
      const keep = (markup: string) => `\u0000${stash.push(markup) - 1}\u0000`;
      let out = part.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, url) => {
        const u = safeUrl(url);
        return u ? keep(`<img src="${u}" alt="${alt}">`) : m;
      });
      out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, label, url) => {
        const u = safeUrl(url);
        return u ? keep(`<a href="${u}">${label}</a>`) : m;
      });
      out = out.replace(/\*\*(.+?)\*\*|__(.+?)__/g, (_m, a, b) => `<strong>${a ?? b}</strong>`);
      out = out.replace(/\*(.+?)\*|_(.+?)_/g, (_m, a, b) => `<em>${a ?? b}</em>`);
      return out.replace(/\u0000(\d+)\u0000/g, (_m, n) => stash[Number(n)]);
    })
    .join("");
}

function render(markdown: string): string {
  const lines = escapeHtml(markdown.replace(/\u0000/g, "")).replace(/\r\n/g, "\n").split("\n");
  const out: string[] = [];
  const para: string[] = [];
  const flush = () => {
    if (para.length) out.push(`<p>${inline(para.join(" "))}</p>`);
    para.length = 0;
  };
  let i = 0;
  while (i < lines.length) {
    const stripped = lines[i].trim();
    if (stripped.startsWith("```")) {
      flush();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      i += 1;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(stripped);
    if (heading) {
      flush();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (stripped.startsWith("&gt;")) {
      flush();
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("&gt;")) quote.push(lines[i++].trim().slice(4).trim());
      out.push(`<blockquote><p>${inline(quote.join(" "))}</p></blockquote>`);
      continue;
    } else if (/^([-*+]|\d+\.)\s+/.test(stripped)) {
      flush();
      const ordered = /^\d+\./.test(stripped);
      const pattern = ordered ? /^\d+\.\s+/ : /^[-*+]\s+/;
      const items: string[] = [];
      while (i < lines.length && pattern.test(lines[i].trim())) items.push(`<li>${inline(lines[i++].trim().replace(pattern, ""))}</li>`);
      out.push(ordered ? `<ol>${items.join("")}</ol>` : `<ul>${items.join("")}</ul>`);
      continue;
    } else if (!stripped) {
      flush();
    } else {
      para.push(stripped);
    }
    i += 1;
  }
  flush();
  return out.join("\n");
}

// auth -----------------------------------------------------------------------------------------------------------

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
app.use(express.json({ limit: "1mb" }));
app.use((err: any, _req: Request, res: Response, next: NextFunction) => {
  if (err?.type === "entity.parse.failed") return fail(res, 400, "invalid JSON");
  if (err?.type === "entity.too.large") return fail(res, 413, "request too large");
  next(err);
});

app.get("/api/health", (_req, res) => {
  res.json({ status: "ok" });
});

app.post("/api/auth/register", (req, res) => {
  const body = req.body;
  if (body === null || typeof body !== "object" || Array.isArray(body)) return fail(res, 400, "JSON object expected");
  const { username, password } = body;
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

// notes ----------------------------------------------------------------------------------------------------------

const NOTE = "SELECT notes.*, users.username AS author FROM notes JOIN users ON users.id = notes.user_id";
const summary = (r: Row) => ({ id: r.id, title: r.title, published: Boolean(r.published), updated_at: r.updated_at });
const noteJson = (r: Row) => ({ ...summary(r), body: r.body, author: r.author });
const nextSeq = () => (db.prepare("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM notes").get() as Row).n;

function fields(body: any, partial: boolean): Record<string, any> | string {
  if (body === null || typeof body !== "object" || Array.isArray(body)) return "JSON object expected";
  const out: Record<string, any> = {};
  if ("title" in body || !partial) {
    if (typeof body.title !== "string" || body.title.length < 1 || body.title.length > 120) return "title must be 1-120 characters";
    out.title = body.title;
  }
  if ("body" in body || !partial) {
    if (typeof body.body !== "string" || !body.body.trim() || body.body.length > 50000)
      return "body must be 1-50000 characters and not blank";
    out.body = body.body;
  }
  if ("published" in body) {
    if (body.published !== null && typeof body.published !== "boolean") return "published must be true or false";
    out.published = body.published ? 1 : 0;
  }
  return out;
}

const owned = (id: string, uid: number) =>
  /^\d+$/.test(id) ? (db.prepare(`${NOTE} WHERE notes.id = ? AND notes.user_id = ?`).get(Number(id), uid) as Row | undefined) : undefined;

const sendHtml = (res: Response, markdown: string) => res.type("text/html; charset=utf-8").send(render(markdown));

app.get("/api/notes", auth, (req: AuthedRequest, res) => {
  const rows = db.prepare(`${NOTE} WHERE notes.user_id = ? ORDER BY notes.seq DESC`).all(req.uid!) as Row[];
  res.json({ items: rows.map(summary) });
});

app.post("/api/notes", auth, (req: AuthedRequest, res) => {
  const f = fields(req.body, false);
  if (typeof f === "string") return fail(res, 400, f);
  const info = db
    .prepare("INSERT INTO notes (user_id, title, body, published, updated_at, seq) VALUES (?, ?, ?, ?, ?, ?)")
    .run(req.uid!, f.title, f.body, f.published ?? 0, now(), nextSeq());
  res.status(201).json(noteJson(db.prepare(`${NOTE} WHERE notes.id = ?`).get(info.lastInsertRowid) as Row));
});

app.get("/api/notes/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  res.json(noteJson(row));
});

app.patch("/api/notes/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  const f = fields(req.body, true);
  if (typeof f === "string") return fail(res, 400, f);
  f.updated_at = now();
  f.seq = nextSeq();
  const keys = Object.keys(f);
  db.prepare(`UPDATE notes SET ${keys.map((k) => `${k} = ?`).join(", ")} WHERE id = ?`).run(...keys.map((k) => f[k]), row.id);
  res.json(noteJson(db.prepare(`${NOTE} WHERE notes.id = ?`).get(row.id) as Row));
});

app.delete("/api/notes/:id", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  db.prepare("DELETE FROM notes WHERE id = ?").run(row.id);
  res.status(204).end();
});

app.get("/api/notes/:id/html", auth, (req: AuthedRequest, res) => {
  const row = owned(String(req.params.id), req.uid!);
  if (!row) return fail(res, 404, "not found");
  sendHtml(res, row.body);
});

app.get("/api/public/notes/:id/html", (req, res) => {
  const id = String(req.params.id);
  const row = /^\d+$/.test(id)
    ? (db.prepare(`${NOTE} WHERE notes.id = ? AND notes.published = 1`).get(Number(id)) as Row | undefined)
    : undefined;
  if (!row) return fail(res, 404, "not found");
  sendHtml(res, row.body);
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

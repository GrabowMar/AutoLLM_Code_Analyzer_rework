// Go reference for notes_markdown_render (oracle reference, contract_bearer); a port of the Python reference core.
// Errors are {"detail": "<message>"}; markdown.go renders the bodies. seq orders the list by the latest change.
package main

import (
	"net/http"
	"strings"
)

const schema = `
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
  body TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0);
`

const noteSelect = "SELECT notes.id, notes.title, notes.body, notes.published, notes.updated_at, users.username" +
	" FROM notes JOIN users ON users.id = notes.user_id"

type note struct {
	id                           int64
	title, body, updated, author string
	published                    bool
}

type scanner interface{ Scan(dest ...any) error }

func scanNote(row scanner) (*note, error) {
	var n note
	var published int
	if err := row.Scan(&n.id, &n.title, &n.body, &published, &n.updated, &n.author); err != nil {
		return nil, err
	}
	n.published = published != 0
	return &n, nil
}

func (n *note) summary() map[string]any {
	return map[string]any{"id": n.id, "title": n.title, "published": n.published, "updated_at": n.updated}
}

func (n *note) full() map[string]any {
	out := n.summary()
	out["body"] = n.body
	out["author"] = n.author
	return out
}

// noteFields validates a note body; returns (column/value pairs, error message).
func noteFields(body map[string]any, partial bool) ([][2]any, string) {
	if body == nil {
		return nil, "JSON object expected"
	}
	var out [][2]any
	if _, has := body["title"]; has || !partial {
		title, ok := body["title"].(string)
		if !ok || strLen(title) < 1 || strLen(title) > 120 {
			return nil, "title must be 1-120 characters"
		}
		out = append(out, [2]any{"title", title})
	}
	if _, has := body["body"]; has || !partial {
		text, ok := body["body"].(string)
		if !ok || strings.TrimSpace(text) == "" || strLen(text) > 50000 {
			return nil, "body must be 1-50000 characters and not blank"
		}
		out = append(out, [2]any{"body", text})
	}
	if v, has := body["published"]; has {
		flag := 0
		if v != nil {
			b, ok := v.(bool)
			if !ok {
				return nil, "published must be true or false"
			}
			if b {
				flag = 1
			}
		}
		out = append(out, [2]any{"published", flag})
	}
	return out, ""
}

func nextSeq() int64 {
	var n int64
	_ = db.QueryRow("SELECT COALESCE(MAX(seq), 0) + 1 FROM notes").Scan(&n)
	return n
}

func ownedNote(r *http.Request, uid int64) *note {
	id, ok := pathID(r, "id")
	if !ok {
		return nil
	}
	n, err := scanNote(db.QueryRow(noteSelect+" WHERE notes.id = ? AND notes.user_id = ?", id, uid))
	if err != nil {
		return nil
	}
	return n
}

func listNotes(w http.ResponseWriter, _ *http.Request, uid int64) {
	rows, err := db.Query(noteSelect+" WHERE notes.user_id = ? ORDER BY notes.seq DESC", uid)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		if n, err := scanNote(rows); err == nil {
			items = append(items, n.summary())
		}
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func createNote(w http.ResponseWriter, r *http.Request, uid int64) {
	fields, problem := noteFields(readObject(r), false)
	if problem != "" {
		fail(w, 400, problem)
		return
	}
	values := map[string]any{"published": 0}
	for _, f := range fields {
		values[f[0].(string)] = f[1]
	}
	res, err := db.Exec("INSERT INTO notes (user_id, title, body, published, updated_at, seq) VALUES (?, ?, ?, ?, ?, ?)",
		uid, values["title"], values["body"], values["published"], now(), nextSeq())
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	id, _ := res.LastInsertId()
	n, err := scanNote(db.QueryRow(noteSelect+" WHERE notes.id = ?", id))
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 201, n.full())
}

func getNote(w http.ResponseWriter, r *http.Request, uid int64) {
	if n := ownedNote(r, uid); n != nil {
		writeJSON(w, 200, n.full())
		return
	}
	fail(w, 404, "not found")
}

func updateNote(w http.ResponseWriter, r *http.Request, uid int64) {
	n := ownedNote(r, uid)
	if n == nil {
		fail(w, 404, "not found")
		return
	}
	fields, problem := noteFields(readObject(r), true)
	if problem != "" {
		fail(w, 400, problem)
		return
	}
	fields = append(fields, [2]any{"updated_at", now()}, [2]any{"seq", nextSeq()})
	sets := make([]string, 0, len(fields))
	args := make([]any, 0, len(fields)+1)
	for _, f := range fields {
		sets = append(sets, f[0].(string)+" = ?")
		args = append(args, f[1])
	}
	if _, err := db.Exec("UPDATE notes SET "+strings.Join(sets, ", ")+" WHERE id = ?", append(args, n.id)...); err != nil {
		fail(w, 500, "internal error")
		return
	}
	updated, err := scanNote(db.QueryRow(noteSelect+" WHERE notes.id = ?", n.id))
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 200, updated.full())
}

func deleteNote(w http.ResponseWriter, r *http.Request, uid int64) {
	n := ownedNote(r, uid)
	if n == nil {
		fail(w, 404, "not found")
		return
	}
	if _, err := db.Exec("DELETE FROM notes WHERE id = ?", n.id); err != nil {
		fail(w, 500, "internal error")
		return
	}
	noContent(w)
}

func sendHTML(w http.ResponseWriter, markdown string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(200)
	_, _ = w.Write([]byte(render(markdown)))
}

func renderNote(w http.ResponseWriter, r *http.Request, uid int64) {
	if n := ownedNote(r, uid); n != nil {
		sendHTML(w, n.body)
		return
	}
	fail(w, 404, "not found")
}

func renderPublic(w http.ResponseWriter, r *http.Request) {
	id, ok := pathID(r, "id")
	if ok {
		if n, err := scanNote(db.QueryRow(noteSelect+" WHERE notes.id = ? AND notes.published = 1", id)); err == nil {
			sendHTML(w, n.body)
			return
		}
	}
	fail(w, 404, "not found")
}

func main() {
	openDB(schema)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/notes", authed(listNotes))
	mux.HandleFunc("POST /api/notes", authed(createNote))
	mux.HandleFunc("GET /api/notes/{id}", authed(getNote))
	mux.HandleFunc("PATCH /api/notes/{id}", authed(updateNote))
	mux.HandleFunc("DELETE /api/notes/{id}", authed(deleteNote))
	mux.HandleFunc("GET /api/notes/{id}/html", authed(renderNote))
	mux.HandleFunc("GET /api/public/notes/{id}/html", renderPublic)
	serve(mux)
}

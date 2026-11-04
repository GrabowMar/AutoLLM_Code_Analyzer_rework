// Go reference for crud_todo_list (conformance kit, contract_bearer); a port of the Flask reference.
package main

import (
	"database/sql"
	"fmt"
	"net/http"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const schema = `
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
  description TEXT, priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT, completed INTEGER NOT NULL DEFAULT 0,
  completed_at TEXT, created_at TEXT NOT NULL);
`

var dateRe = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}$`)

func validPriority(p string) bool { return p == "low" || p == "medium" || p == "high" }

func validDate(v any) bool {
	s, ok := v.(string)
	if !ok || !dateRe.MatchString(s) {
		return false
	}
	_, err := time.Parse("2006-01-02", s)
	return err == nil
}

func validate(body map[string]any, partial bool) string {
	if body == nil {
		return "JSON object expected"
	}
	if _, has := body["title"]; has || !partial {
		title, ok := body["title"].(string)
		if !ok || strLen(title) < 1 || strLen(title) > 255 {
			return "title must be 1-255 characters"
		}
	}
	if v, ok := body["description"]; ok && v != nil {
		s, isStr := v.(string)
		if !isStr || strLen(s) > 2000 {
			return "description must be at most 2000 characters"
		}
	}
	if v, ok := body["priority"]; ok && v != nil {
		s, isStr := v.(string)
		if !isStr || !validPriority(s) {
			return "priority must be low, medium or high"
		}
	}
	if v, ok := body["due_date"]; ok && v != nil && !validDate(v) {
		return "due_date must be YYYY-MM-DD"
	}
	if v, ok := body["completed"]; ok && v != nil {
		if _, isBool := v.(bool); !isBool {
			return "completed must be a boolean"
		}
	}
	return ""
}

const todoCols = "id, title, description, priority, due_date, completed, completed_at, created_at"

type scanner interface{ Scan(dest ...any) error }

func scanTodo(row scanner) (map[string]any, error) {
	var id int64
	var title, priority, createdAt string
	var description, dueDate, completedAt sql.NullString
	var completed int
	if err := row.Scan(&id, &title, &description, &priority, &dueDate, &completed, &completedAt, &createdAt); err != nil {
		return nil, err
	}
	return map[string]any{
		"id": id, "title": title, "description": nullable(description), "priority": priority,
		"due_date": nullable(dueDate), "completed": completed != 0, "completed_at": nullable(completedAt),
		"created_at": createdAt,
	}, nil
}

func owned(id, uid int64) (map[string]any, error) {
	return scanTodo(db.QueryRow("SELECT "+todoCols+" FROM todos WHERE id = ? AND user_id = ?", id, uid))
}

func listTodos(w http.ResponseWriter, r *http.Request, uid int64) {
	where := []string{"user_id = ?"}
	args := []any{uid}
	q := r.URL.Query()
	if q.Has("completed") {
		where = append(where, "completed = ?")
		v := strings.ToLower(q.Get("completed"))
		if v == "true" || v == "1" {
			args = append(args, 1)
		} else {
			args = append(args, 0)
		}
	}
	if q.Has("priority") {
		p := q.Get("priority")
		if !validPriority(p) {
			fail(w, 400, "invalid priority")
			return
		}
		where = append(where, "priority = ?")
		args = append(args, p)
	}
	page, pageSize := 1, 50
	var err error
	if q.Has("page") {
		if page, err = strconv.Atoi(q.Get("page")); err != nil {
			fail(w, 400, "page and page_size must be integers")
			return
		}
	}
	if q.Has("page_size") {
		if pageSize, err = strconv.Atoi(q.Get("page_size")); err != nil {
			fail(w, 400, "page and page_size must be integers")
			return
		}
	}
	page = max(1, page)
	pageSize = min(200, max(1, pageSize))
	clause := strings.Join(where, " AND ")
	var total, remaining int
	if err := db.QueryRow("SELECT COUNT(*) FROM todos WHERE "+clause, args...).Scan(&total); err != nil {
		fail(w, 500, "internal error")
		return
	}
	rows, err := db.Query(
		"SELECT "+todoCols+" FROM todos WHERE "+clause+" ORDER BY completed ASC, id DESC LIMIT ? OFFSET ?",
		append(args, pageSize, (page-1)*pageSize)...,
	)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	items := []map[string]any{}
	for rows.Next() {
		item, err := scanTodo(rows)
		if err != nil {
			rows.Close()
			fail(w, 500, "internal error")
			return
		}
		items = append(items, item)
	}
	rows.Close()
	if err := db.QueryRow("SELECT COUNT(*) FROM todos WHERE user_id = ? AND completed = 0", uid).Scan(&remaining); err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 200, map[string]any{"items": items, "total": total, "page": page, "page_size": pageSize, "remaining": remaining})
}

func createTodo(w http.ResponseWriter, r *http.Request, uid int64) {
	body := readObject(r)
	if problem := validate(body, false); problem != "" {
		fail(w, 400, problem)
		return
	}
	completed, _ := body["completed"].(bool)
	priority, _ := body["priority"].(string)
	if priority == "" {
		priority = "medium"
	}
	var completedAt any
	if completed {
		completedAt = now()
	}
	done := 0
	if completed {
		done = 1
	}
	res, err := db.Exec(
		"INSERT INTO todos (user_id, title, description, priority, due_date, completed, completed_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
		uid, body["title"], body["description"], priority, body["due_date"], done, completedAt, now(),
	)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	id, _ := res.LastInsertId()
	todo, err := owned(id, uid)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 201, todo)
}

func getTodo(w http.ResponseWriter, r *http.Request, uid int64) {
	id, ok := pathID(r, "id")
	if !ok {
		fail(w, 404, "not found")
		return
	}
	todo, err := owned(id, uid)
	if err != nil {
		fail(w, 404, "not found")
		return
	}
	writeJSON(w, 200, todo)
}

func updateTodo(w http.ResponseWriter, r *http.Request, uid int64) {
	id, ok := pathID(r, "id")
	if !ok {
		fail(w, 404, "not found")
		return
	}
	current, err := owned(id, uid)
	if err != nil {
		fail(w, 404, "not found")
		return
	}
	body := readObject(r)
	if problem := validate(body, true); problem != "" {
		fail(w, 400, problem)
		return
	}
	var sets []string
	var args []any
	for _, key := range []string{"title", "description", "priority", "due_date"} {
		if v, has := body[key]; has {
			if key == "priority" && v == nil {
				v = "medium"
			}
			sets = append(sets, key+" = ?")
			args = append(args, v)
		}
	}
	if v, has := body["completed"]; has && v != nil {
		done := v.(bool)
		flag := 0
		if done {
			flag = 1
		}
		sets = append(sets, "completed = ?")
		args = append(args, flag)
		if done && !current["completed"].(bool) {
			sets = append(sets, "completed_at = ?")
			args = append(args, now())
		} else if !done {
			sets = append(sets, "completed_at = ?")
			args = append(args, nil)
		}
	}
	if len(sets) > 0 {
		if _, err := db.Exec(fmt.Sprintf("UPDATE todos SET %s WHERE id = ?", strings.Join(sets, ", ")), append(args, id)...); err != nil {
			fail(w, 500, "internal error")
			return
		}
	}
	todo, err := owned(id, uid)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 200, todo)
}

func deleteTodo(w http.ResponseWriter, r *http.Request, uid int64) {
	id, ok := pathID(r, "id")
	if !ok {
		fail(w, 404, "not found")
		return
	}
	if _, err := owned(id, uid); err != nil {
		fail(w, 404, "not found")
		return
	}
	if _, err := db.Exec("DELETE FROM todos WHERE id = ?", id); err != nil {
		fail(w, 500, "internal error")
		return
	}
	noContent(w)
}

func main() {
	openDB(schema)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/todos", authed(listTodos))
	mux.HandleFunc("POST /api/todos", authed(createTodo))
	mux.HandleFunc("GET /api/todos/{id}", authed(getTodo))
	mux.HandleFunc("PATCH /api/todos/{id}", authed(updateTodo))
	mux.HandleFunc("DELETE /api/todos/{id}", authed(deleteTodo))
	serve(mux)
}

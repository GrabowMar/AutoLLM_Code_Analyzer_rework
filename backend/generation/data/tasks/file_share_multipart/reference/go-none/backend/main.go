// Go reference for file_share_multipart (conformance kit, contract_bearer); a port of the Flask reference.
//
// Requests over 10 MiB are refused with 413 (http.MaxBytesReader). Stored files are named by a random id under
// data/files; the client's filename is metadata only.
package main

import (
	"crypto/rand"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"mime"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"

	"encoding/base64"
)

const maxUpload = 10 * 1024 * 1024

const schema = `
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, stored TEXT NOT NULL,
  filename TEXT, description TEXT, size_bytes INTEGER, content_type TEXT, uploaded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL);
`

var unsafeName = regexp.MustCompile(`[^A-Za-z0-9._-]`)

func filesDir() string { return filepath.Join(dataDir, "files") }

type storedFile struct {
	id, size                                     int64
	stored                                       string
	filename, description, contentType, uploaded sql.NullString
}

const fileCols = "id, stored, filename, description, size_bytes, content_type, uploaded_at"

type scanner interface{ Scan(dest ...any) error }

func scanFile(row scanner) (*storedFile, error) {
	var f storedFile
	var size sql.NullInt64
	if err := row.Scan(&f.id, &f.stored, &f.filename, &f.description, &size, &f.contentType, &f.uploaded); err != nil {
		return nil, err
	}
	f.size = size.Int64
	return &f, nil
}

func (f *storedFile) json() map[string]any {
	return map[string]any{
		"id": f.id, "description": nullable(f.description), "filename": nullable(f.filename), "size_bytes": f.size,
		"content_type": nullable(f.contentType), "uploaded_at": nullable(f.uploaded),
		"download_url": fmt.Sprintf("/api/files/%d/download", f.id),
	}
}

func owned(id, uid int64) (*storedFile, error) {
	return scanFile(db.QueryRow("SELECT "+fileCols+" FROM files WHERE id = ? AND user_id = ?", id, uid))
}

func send(w http.ResponseWriter, f *storedFile) {
	data, err := os.ReadFile(filepath.Join(filesDir(), f.stored))
	if err != nil {
		fail(w, 404, "not found")
		return
	}
	name := "file"
	if f.filename.Valid && f.filename.String != "" {
		name = f.filename.String
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="`+unsafeName.ReplaceAllString(name, "_")+`"`)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

func tooLarge(err error) bool {
	var maxErr *http.MaxBytesError
	return errors.As(err, &maxErr)
}

func upload(w http.ResponseWriter, r *http.Request, uid int64) {
	if r.ContentLength > maxUpload {
		fail(w, 413, "file too large")
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxUpload)
	if err := r.ParseMultipartForm(maxUpload); err != nil {
		if tooLarge(err) {
			fail(w, 413, "file too large")
		} else {
			fail(w, 400, "file is required")
		}
		return
	}
	defer r.MultipartForm.RemoveAll()
	part, header, err := r.FormFile("file")
	if err != nil {
		fail(w, 400, "file is required")
		return
	}
	defer part.Close()
	var description any
	if values, ok := r.MultipartForm.Value["description"]; ok && len(values) > 0 {
		if strLen(values[0]) > 300 {
			fail(w, 400, "description must be at most 300 characters")
			return
		}
		description = values[0]
	}
	data, err := io.ReadAll(part)
	if err != nil {
		fail(w, 400, "unreadable upload")
		return
	}
	if len(data) == 0 {
		fail(w, 400, "the file is empty")
		return
	}
	name := header.Filename
	if i := strings.LastIndex(name, "/"); i >= 0 {
		name = name[i+1:]
	}
	if name == "" {
		name = "file"
	}
	var contentType any
	if mediaType, _, err := mime.ParseMediaType(header.Header.Get("Content-Type")); err == nil {
		contentType = mediaType
	}
	stored := randomHex(16)
	if err := os.MkdirAll(filesDir(), 0o755); err != nil {
		fail(w, 500, "internal error")
		return
	}
	if err := os.WriteFile(filepath.Join(filesDir(), stored), data, 0o600); err != nil {
		fail(w, 500, "internal error")
		return
	}
	res, err := db.Exec(
		"INSERT INTO files (user_id, stored, filename, description, size_bytes, content_type, uploaded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
		uid, stored, name, description, len(data), contentType, now())
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	id, _ := res.LastInsertId()
	f, err := owned(id, uid)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 201, f.json())
}

func listFiles(w http.ResponseWriter, r *http.Request, uid int64) {
	q := r.URL.Query()
	page, pageSize := 1, 20
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
	pageSize = min(100, max(1, pageSize))
	var total int
	if err := db.QueryRow("SELECT COUNT(*) FROM files WHERE user_id = ?", uid).Scan(&total); err != nil {
		fail(w, 500, "internal error")
		return
	}
	rows, err := db.Query("SELECT "+fileCols+" FROM files WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
		uid, pageSize, (page-1)*pageSize)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		if f, err := scanFile(rows); err == nil {
			items = append(items, f.json())
		}
	}
	writeJSON(w, 200, map[string]any{"items": items, "total": total, "page": page, "page_size": pageSize})
}

func ownedFromPath(w http.ResponseWriter, r *http.Request, uid int64) *storedFile {
	id, ok := pathID(r, "id")
	if ok {
		if f, err := owned(id, uid); err == nil {
			return f
		}
	}
	fail(w, 404, "not found")
	return nil
}

func getFile(w http.ResponseWriter, r *http.Request, uid int64) {
	if f := ownedFromPath(w, r, uid); f != nil {
		writeJSON(w, 200, f.json())
	}
}

func download(w http.ResponseWriter, r *http.Request, uid int64) {
	if f := ownedFromPath(w, r, uid); f != nil {
		send(w, f)
	}
}

func deleteFile(w http.ResponseWriter, r *http.Request, uid int64) {
	f := ownedFromPath(w, r, uid)
	if f == nil {
		return
	}
	if _, err := db.Exec("DELETE FROM shares WHERE file_id = ?", f.id); err != nil {
		fail(w, 500, "internal error")
		return
	}
	if _, err := db.Exec("DELETE FROM files WHERE id = ?", f.id); err != nil {
		fail(w, 500, "internal error")
		return
	}
	_ = os.Remove(filepath.Join(filesDir(), f.stored))
	noContent(w)
}

func createShare(w http.ResponseWriter, r *http.Request, uid int64) {
	f := ownedFromPath(w, r, uid)
	if f == nil {
		return
	}
	buf := make([]byte, 24)
	if _, err := rand.Read(buf); err != nil {
		fail(w, 500, "internal error")
		return
	}
	token := base64.RawURLEncoding.EncodeToString(buf)
	if _, err := db.Exec("INSERT INTO shares (token, file_id) VALUES (?, ?)", token, f.id); err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 201, map[string]string{"token": token, "url": "/api/shared/" + token})
}

func deleteShare(w http.ResponseWriter, r *http.Request, uid int64) {
	f := ownedFromPath(w, r, uid)
	if f == nil {
		return
	}
	res, err := db.Exec("DELETE FROM shares WHERE token = ? AND file_id = ?", r.PathValue("token"), f.id)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	if n, _ := res.RowsAffected(); n == 0 {
		fail(w, 404, "not found")
		return
	}
	noContent(w)
}

func shared(w http.ResponseWriter, r *http.Request) {
	f, err := scanFile(db.QueryRow(
		"SELECT files.id, files.stored, files.filename, files.description, files.size_bytes, files.content_type,"+
			" files.uploaded_at FROM shares JOIN files ON files.id = shares.file_id WHERE shares.token = ?",
		r.PathValue("token")))
	if err != nil {
		fail(w, 404, "not found")
		return
	}
	send(w, f)
}

func main() {
	openDB(schema)
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/files", authed(upload))
	mux.HandleFunc("GET /api/files", authed(listFiles))
	mux.HandleFunc("GET /api/files/{id}", authed(getFile))
	mux.HandleFunc("GET /api/files/{id}/download", authed(download))
	mux.HandleFunc("DELETE /api/files/{id}", authed(deleteFile))
	mux.HandleFunc("POST /api/files/{id}/shares", authed(createShare))
	mux.HandleFunc("DELETE /api/files/{id}/shares/{token}", authed(deleteShare))
	mux.HandleFunc("GET /api/shared/{token}", shared)
	serve(mux)
}

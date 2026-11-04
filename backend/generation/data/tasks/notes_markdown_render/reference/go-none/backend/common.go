package main

// Shared plumbing of the Go kit references: SQLite, password hashing, bearer tokens, JSON replies.

import (
	"crypto/hmac"
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"html/template"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

var (
	// errorKey names the message field of error bodies.
	errorKey = "detail"
	db       *sql.DB
	secret   []byte
	dataDir  string
	pages    = template.Must(template.ParseFiles("backend/templates/index.html"))
)

func randomHex(n int) string {
	buf := make([]byte, n)
	if _, err := rand.Read(buf); err != nil {
		panic(err)
	}
	return hex.EncodeToString(buf)
}

func openDB(schema string) {
	dataDir = os.Getenv("DATA_DIR")
	if dataDir == "" {
		dataDir = "data"
	}
	if err := os.MkdirAll(dataDir, 0o755); err != nil {
		log.Fatal(err)
	}
	if key := os.Getenv("SECRET_KEY"); key != "" {
		secret = []byte(key)
	} else {
		secret = []byte(randomHex(32))
	}
	var err error
	db, err = sql.Open("sqlite", "file:"+filepath.Join(dataDir, "app.db")+"?_pragma=busy_timeout(5000)&_pragma=foreign_keys(1)")
	if err != nil {
		log.Fatal(err)
	}
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(schema); err != nil {
		log.Fatal(err)
	}
}

func now() string { return time.Now().UTC().Format("2006-01-02T15:04:05Z") }

func hashPassword(password, salt string) string {
	if salt == "" {
		salt = randomHex(16)
	}
	key, err := pbkdf2.Key(sha256.New, password, []byte(salt), 100_000, 32)
	if err != nil {
		panic(err)
	}
	return salt + "$" + hex.EncodeToString(key)
}

func sign(uid int64) string {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(strconv.FormatInt(uid, 10)))
	return strconv.FormatInt(uid, 10) + "." + hex.EncodeToString(mac.Sum(nil))
}

// currentUser returns the id of the user named by a valid bearer token, or 0.
func currentUser(r *http.Request) int64 {
	header := r.Header.Get("Authorization")
	if !strings.HasPrefix(header, "Bearer ") {
		return 0
	}
	token := header[7:]
	raw, _, _ := strings.Cut(token, ".")
	uid, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || uid <= 0 || raw != strconv.FormatInt(uid, 10) {
		return 0
	}
	if !hmac.Equal([]byte(sign(uid)), []byte(token)) {
		return 0
	}
	var one int
	if db.QueryRow("SELECT 1 FROM users WHERE id = ?", uid).Scan(&one) != nil {
		return 0
	}
	return uid
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func fail(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{errorKey: message})
}

func noContent(w http.ResponseWriter) { w.WriteHeader(http.StatusNoContent) }

// readObject decodes a JSON object body; nil when the body is not one.
func readObject(r *http.Request) map[string]any {
	data, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		return nil
	}
	var body map[string]any
	if json.Unmarshal(data, &body) != nil {
		return nil
	}
	return body
}

func strLen(s string) int { return utf8.RuneCountInString(s) }

// optString: (value, present-and-non-null, type ok).
func optString(body map[string]any, key string) (string, bool, bool) {
	v, ok := body[key]
	if !ok || v == nil {
		return "", false, true
	}
	s, isStr := v.(string)
	return s, true, isStr
}

func pathID(r *http.Request, name string) (int64, bool) {
	raw := r.PathValue(name)
	id, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || id < 0 || raw != strconv.FormatInt(id, 10) {
		return 0, false
	}
	return id, true
}

func nullable(v sql.NullString) any {
	if v.Valid {
		return v.String
	}
	return nil
}

func health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func index(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := pages.ExecuteTemplate(w, "index.html", map[string]string{"Title": "App"}); err != nil {
		http.Error(w, "template error", http.StatusInternalServerError)
	}
}

func register(w http.ResponseWriter, r *http.Request) {
	body := readObject(r)
	if body == nil {
		fail(w, 400, "JSON object expected")
		return
	}
	username, ok := body["username"].(string)
	if !ok || strLen(username) < 1 || strLen(username) > 50 {
		fail(w, 400, "username must be 1-50 characters")
		return
	}
	password, ok := body["password"].(string)
	if !ok || password == "" {
		fail(w, 400, "password is required")
		return
	}
	res, err := db.Exec("INSERT INTO users (username, pw) VALUES (?, ?)", username, hashPassword(password, ""))
	if err != nil {
		if strings.Contains(err.Error(), "UNIQUE") {
			fail(w, 409, "username taken")
			return
		}
		fail(w, 500, "internal error")
		return
	}
	id, _ := res.LastInsertId()
	writeJSON(w, 201, map[string]any{"id": id, "username": username})
}

func login(w http.ResponseWriter, r *http.Request) {
	body := readObject(r)
	username, ok1 := body["username"].(string)
	password, ok2 := body["password"].(string)
	if body == nil || !ok1 || !ok2 {
		fail(w, 401, "invalid credentials")
		return
	}
	var id int64
	var stored string
	err := db.QueryRow("SELECT id, pw FROM users WHERE username = ?", username).Scan(&id, &stored)
	if errors.Is(err, sql.ErrNoRows) || err != nil {
		fail(w, 401, "invalid credentials")
		return
	}
	salt, _, _ := strings.Cut(stored, "$")
	if !hmac.Equal([]byte(hashPassword(password, salt)), []byte(stored)) {
		fail(w, 401, "invalid credentials")
		return
	}
	writeJSON(w, 200, map[string]string{"token": sign(id)})
}

// authed wraps a handler that needs a signed-in user (401 otherwise).
func authed(next func(http.ResponseWriter, *http.Request, int64)) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		uid := currentUser(r)
		if uid == 0 {
			fail(w, 401, "authentication required")
			return
		}
		next(w, r, uid)
	}
}

func serve(mux *http.ServeMux) {
	mux.HandleFunc("GET /api/health", health)
	mux.HandleFunc("POST /api/auth/register", register)
	mux.HandleFunc("POST /api/auth/login", login)
	mux.HandleFunc("GET /", index)
	port := os.Getenv("PORT")
	if port == "" {
		port = "8000"
	}
	log.Printf("listening on %s", port)
	log.Fatal(http.ListenAndServe("0.0.0.0:"+port, mux))
}

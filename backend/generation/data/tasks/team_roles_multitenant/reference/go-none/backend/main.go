// Go reference for team_roles_multitenant (conformance kit, contract_bearer); a port of the Python core.
//
// Roles: owner > admin > member > viewer. Owners and admins manage members (an admin can neither grant nor touch
// the owner role); owners, admins and members write projects; owners and admins delete them; every member reads.
// A non-member sees 404 for anything in the organization (no tenant leaks); the last owner cannot be removed or
// demoted (409).
package main

import (
	"database/sql"
	"net/http"
	"strings"
)

const schema = `
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS orgs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  role TEXT NOT NULL, UNIQUE (org_id, user_id));
CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL,
  description TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
`

func validRole(v any) (string, bool) {
	s, ok := v.(string)
	return s, ok && (s == "owner" || s == "admin" || s == "member" || s == "viewer")
}

func validStatus(s string) bool { return s == "active" || s == "archived" }

// boundedString checks body[key] is a string of 1..max characters.
func boundedString(body map[string]any, key string, maxLen int) (string, bool) {
	s, ok := body[key].(string)
	return s, ok && strLen(s) >= 1 && strLen(s) <= maxLen
}

func roleIn(orgID, uid int64) string {
	var role string
	if db.QueryRow("SELECT role FROM members WHERE org_id = ? AND user_id = ?", orgID, uid).Scan(&role) != nil {
		return ""
	}
	return role
}

func orgParam(w http.ResponseWriter, r *http.Request, uid int64) (int64, string, bool) {
	orgID, ok := pathID(r, "org_id")
	role := ""
	if ok {
		role = roleIn(orgID, uid)
	}
	if role == "" {
		fail(w, 404, "not found")
		return 0, "", false
	}
	return orgID, role, true
}

func createOrg(w http.ResponseWriter, r *http.Request, uid int64) {
	body := readObject(r)
	if body == nil {
		fail(w, 400, "JSON object expected")
		return
	}
	name, ok := boundedString(body, "name", 100)
	if !ok {
		fail(w, 400, "name must be 1-100 characters")
		return
	}
	created := now()
	res, err := db.Exec("INSERT INTO orgs (name, created_at) VALUES (?, ?)", name, created)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	id, _ := res.LastInsertId()
	if _, err := db.Exec("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, 'owner')", id, uid); err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 201, map[string]any{"id": id, "name": name, "created_at": created, "role": "owner"})
}

func listOrgs(w http.ResponseWriter, _ *http.Request, uid int64) {
	rows, err := db.Query(
		"SELECT orgs.id, orgs.name, orgs.created_at, members.role FROM orgs JOIN members ON members.org_id = orgs.id"+
			" WHERE members.user_id = ? ORDER BY orgs.id", uid)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id int64
		var name, created, role string
		if rows.Scan(&id, &name, &created, &role) == nil {
			items = append(items, map[string]any{"id": id, "name": name, "created_at": created, "role": role})
		}
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func listMembers(w http.ResponseWriter, r *http.Request, uid int64) {
	orgID, _, ok := orgParam(w, r, uid)
	if !ok {
		return
	}
	rows, err := db.Query(
		"SELECT members.id, users.username, members.role FROM members JOIN users ON users.id = members.user_id"+
			" WHERE members.org_id = ? ORDER BY members.id", orgID)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		var id int64
		var username, role string
		if rows.Scan(&id, &username, &role) == nil {
			items = append(items, map[string]any{"id": id, "username": username, "role": role})
		}
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func addMember(w http.ResponseWriter, r *http.Request, uid int64) {
	orgID, mine, ok := orgParam(w, r, uid)
	if !ok {
		return
	}
	body := readObject(r)
	if body == nil {
		fail(w, 400, "JSON object expected")
		return
	}
	username, ok := boundedString(body, "username", 50)
	if !ok {
		fail(w, 400, "username must be 1-50 characters")
		return
	}
	role, ok := validRole(body["role"])
	if !ok {
		fail(w, 400, "role must be owner, admin, member or viewer")
		return
	}
	if (mine != "owner" && mine != "admin") || (role == "owner" && mine != "owner") {
		fail(w, 403, "your role does not allow this")
		return
	}
	var target int64
	if db.QueryRow("SELECT id FROM users WHERE username = ?", username).Scan(&target) != nil {
		fail(w, 404, "no such user")
		return
	}
	res, err := db.Exec("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, ?)", orgID, target, role)
	if err != nil {
		fail(w, 409, "already a member")
		return
	}
	id, _ := res.LastInsertId()
	writeJSON(w, 201, map[string]any{"id": id, "username": username, "role": role})
}

type member struct {
	id, userID     int64
	role, username string
}

func targetMember(orgID int64, username string) (member, bool) {
	var m member
	err := db.QueryRow(
		"SELECT members.id, members.role, members.user_id, users.username FROM members"+
			" JOIN users ON users.id = members.user_id WHERE members.org_id = ? AND users.username = ?",
		orgID, username).Scan(&m.id, &m.role, &m.userID, &m.username)
	return m, err == nil
}

func owners(orgID int64) int {
	var n int
	_ = db.QueryRow("SELECT COUNT(*) FROM members WHERE org_id = ? AND role = 'owner'", orgID).Scan(&n)
	return n
}

func updateMember(w http.ResponseWriter, r *http.Request, uid int64) {
	orgID, mine, ok := orgParam(w, r, uid)
	if !ok {
		return
	}
	body := readObject(r)
	role, ok := validRole(body["role"])
	if body == nil || !ok {
		fail(w, 400, "role must be owner, admin, member or viewer")
		return
	}
	target, found := targetMember(orgID, r.PathValue("username"))
	if !found {
		fail(w, 404, "not found")
		return
	}
	if (mine != "owner" && mine != "admin") || (mine != "owner" && (role == "owner" || target.role == "owner")) {
		fail(w, 403, "your role does not allow this")
		return
	}
	if target.role == "owner" && role != "owner" && owners(orgID) == 1 {
		fail(w, 409, "an organization keeps at least one owner")
		return
	}
	if _, err := db.Exec("UPDATE members SET role = ? WHERE id = ?", role, target.id); err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 200, map[string]any{"id": target.id, "username": target.username, "role": role})
}

func removeMember(w http.ResponseWriter, r *http.Request, uid int64) {
	orgID, mine, ok := orgParam(w, r, uid)
	if !ok {
		return
	}
	target, found := targetMember(orgID, r.PathValue("username"))
	if !found {
		fail(w, 404, "not found")
		return
	}
	own := target.userID == uid
	if !own && ((mine != "owner" && mine != "admin") || (mine != "owner" && target.role == "owner")) {
		fail(w, 403, "your role does not allow this")
		return
	}
	if target.role == "owner" && owners(orgID) == 1 {
		fail(w, 409, "an organization keeps at least one owner")
		return
	}
	if _, err := db.Exec("DELETE FROM members WHERE id = ?", target.id); err != nil {
		fail(w, 500, "internal error")
		return
	}
	noContent(w)
}

const projectCols = "id, org_id, name, description, status, created_at"

type scanner interface{ Scan(dest ...any) error }

func scanProject(row scanner) (map[string]any, error) {
	var id, orgID int64
	var name, status, created string
	var description sql.NullString
	if err := row.Scan(&id, &orgID, &name, &description, &status, &created); err != nil {
		return nil, err
	}
	return map[string]any{
		"id": id, "name": name, "description": nullable(description), "status": status, "org_id": orgID,
		"created_at": created,
	}, nil
}

// projectFields validates a project body; returns (columns, error message).
func projectFields(body map[string]any, partial bool) ([][2]any, string) {
	if body == nil {
		return nil, "JSON object expected"
	}
	var out [][2]any
	if _, has := body["name"]; has || !partial {
		name, ok := boundedString(body, "name", 120)
		if !ok {
			return nil, "name must be 1-120 characters"
		}
		out = append(out, [2]any{"name", name})
	}
	if v, has := body["description"]; has {
		if v != nil {
			s, ok := v.(string)
			if !ok || strLen(s) > 2000 {
				return nil, "description must be at most 2000 characters"
			}
		}
		out = append(out, [2]any{"description", v})
	}
	if v, has := body["status"]; has {
		status := "active"
		if v != nil {
			s, ok := v.(string)
			if !ok || !validStatus(s) {
				return nil, "status must be active or archived"
			}
			status = s
		}
		out = append(out, [2]any{"status", status})
	}
	return out, ""
}

func listProjects(w http.ResponseWriter, r *http.Request, uid int64) {
	orgID, _, ok := orgParam(w, r, uid)
	if !ok {
		return
	}
	query, args := "SELECT "+projectCols+" FROM projects WHERE org_id = ?", []any{orgID}
	if r.URL.Query().Has("status") {
		status := r.URL.Query().Get("status")
		if !validStatus(status) {
			fail(w, 400, "status must be active or archived")
			return
		}
		query += " AND status = ?"
		args = append(args, status)
	}
	rows, err := db.Query(query+" ORDER BY id DESC", args...)
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	defer rows.Close()
	items := []map[string]any{}
	for rows.Next() {
		if item, err := scanProject(rows); err == nil {
			items = append(items, item)
		}
	}
	writeJSON(w, 200, map[string]any{"items": items})
}

func createProject(w http.ResponseWriter, r *http.Request, uid int64) {
	orgID, mine, ok := orgParam(w, r, uid)
	if !ok {
		return
	}
	fields, problem := projectFields(readObject(r), false)
	if problem != "" {
		fail(w, 400, problem)
		return
	}
	if mine == "viewer" {
		fail(w, 403, "viewers cannot create projects")
		return
	}
	values := map[string]any{"description": nil, "status": "active"}
	for _, f := range fields {
		values[f[0].(string)] = f[1]
	}
	res, err := db.Exec("INSERT INTO projects (org_id, name, description, status, created_at) VALUES (?, ?, ?, ?, ?)",
		orgID, values["name"], values["description"], values["status"], now())
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	id, _ := res.LastInsertId()
	project, err := scanProject(db.QueryRow("SELECT "+projectCols+" FROM projects WHERE id = ?", id))
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 201, project)
}

// visibleProject returns the project and the caller's role in its organization ("" when not visible).
func visibleProject(r *http.Request, uid int64) (map[string]any, string) {
	id, ok := pathID(r, "id")
	if !ok {
		return nil, ""
	}
	project, err := scanProject(db.QueryRow("SELECT "+projectCols+" FROM projects WHERE id = ?", id))
	if err != nil {
		return nil, ""
	}
	role := roleIn(project["org_id"].(int64), uid)
	if role == "" {
		return nil, ""
	}
	return project, role
}

func getProject(w http.ResponseWriter, r *http.Request, uid int64) {
	project, _ := visibleProject(r, uid)
	if project == nil {
		fail(w, 404, "not found")
		return
	}
	writeJSON(w, 200, project)
}

func updateProject(w http.ResponseWriter, r *http.Request, uid int64) {
	project, role := visibleProject(r, uid)
	if project == nil {
		fail(w, 404, "not found")
		return
	}
	fields, problem := projectFields(readObject(r), true)
	if problem != "" {
		fail(w, 400, problem)
		return
	}
	if role == "viewer" {
		fail(w, 403, "viewers cannot edit projects")
		return
	}
	id := project["id"].(int64)
	if len(fields) > 0 {
		sets := make([]string, 0, len(fields))
		args := make([]any, 0, len(fields)+1)
		for _, f := range fields {
			sets = append(sets, f[0].(string)+" = ?")
			args = append(args, f[1])
		}
		if _, err := db.Exec("UPDATE projects SET "+strings.Join(sets, ", ")+" WHERE id = ?", append(args, id)...); err != nil {
			fail(w, 500, "internal error")
			return
		}
	}
	updated, err := scanProject(db.QueryRow("SELECT "+projectCols+" FROM projects WHERE id = ?", id))
	if err != nil {
		fail(w, 500, "internal error")
		return
	}
	writeJSON(w, 200, updated)
}

func deleteProject(w http.ResponseWriter, r *http.Request, uid int64) {
	project, role := visibleProject(r, uid)
	if project == nil {
		fail(w, 404, "not found")
		return
	}
	if role != "owner" && role != "admin" {
		fail(w, 403, "only owners and admins delete projects")
		return
	}
	if _, err := db.Exec("DELETE FROM projects WHERE id = ?", project["id"]); err != nil {
		fail(w, 500, "internal error")
		return
	}
	noContent(w)
}

func main() {
	openDB(schema)
	mux := http.NewServeMux()
	mux.HandleFunc("POST /api/orgs", authed(createOrg))
	mux.HandleFunc("GET /api/orgs", authed(listOrgs))
	mux.HandleFunc("GET /api/orgs/{org_id}/members", authed(listMembers))
	mux.HandleFunc("POST /api/orgs/{org_id}/members", authed(addMember))
	mux.HandleFunc("PATCH /api/orgs/{org_id}/members/{username}", authed(updateMember))
	mux.HandleFunc("DELETE /api/orgs/{org_id}/members/{username}", authed(removeMember))
	mux.HandleFunc("GET /api/orgs/{org_id}/projects", authed(listProjects))
	mux.HandleFunc("POST /api/orgs/{org_id}/projects", authed(createProject))
	mux.HandleFunc("GET /api/projects/{id}", authed(getProject))
	mux.HandleFunc("PATCH /api/projects/{id}", authed(updateProject))
	mux.HandleFunc("DELETE /api/projects/{id}", authed(deleteProject))
	serve(mux)
}

package app;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * Spring Boot reference for team_roles_multitenant (conformance kit, contract_bearer); a port of the Python core.
 *
 * <p>Roles: owner > admin > member > viewer. Owners and admins manage members (an admin can neither grant nor touch
 * the owner role); owners, admins and members write projects; owners and admins delete them; every member reads. A
 * non-member sees 404 for anything in the organization (no tenant leaks); the last owner cannot be removed or demoted
 * (409).
 */
@RestController
public class TeamController {

    static {
        Db.init("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orgs (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL, role TEXT NOT NULL, UNIQUE (org_id, user_id));
            CREATE TABLE IF NOT EXISTS projects (id INTEGER PRIMARY KEY, org_id INTEGER NOT NULL, name TEXT NOT NULL,
              description TEXT, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL)
            """);
    }

    private static final Set<String> ROLES = Set.of("owner", "admin", "member", "viewer");
    private static final Set<String> STATUSES = Set.of("active", "archived");
    private static final String PROJECT = "SELECT id, name, description, status, org_id, created_at FROM projects";

    private static String role(long orgId, long uid) {
        Map<String, Object> row = Db.one("SELECT role FROM members WHERE org_id = ? AND user_id = ?", orgId, uid);
        return row == null ? null : (String) row.get("role");
    }

    private static boolean manager(String role) {
        return "owner".equals(role) || "admin".equals(role);
    }

    private static ResponseEntity<Object> unauthenticated() {
        return Api.error(401, "authentication required");
    }

    private static ResponseEntity<Object> notFound() {
        return Api.error(404, "not found");
    }

    @PostMapping("/api/orgs")
    public ResponseEntity<Object> createOrg(HttpServletRequest request, @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        Map<String, Object> body = Api.object(raw);
        if (body == null) {
            return Api.error(400, "JSON object expected");
        }
        if (!Api.bounded(body, "name", 100)) {
            return Api.error(400, "name must be 1-100 characters");
        }
        String created = Api.now();
        long id = Db.update("INSERT INTO orgs (name, created_at) VALUES (?, ?)", body.get("name"), created);
        Db.update("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, 'owner')", id, uid);
        return Api.created(Api.obj("id", id, "name", body.get("name"), "created_at", created, "role", "owner"));
    }

    @GetMapping("/api/orgs")
    public ResponseEntity<Object> listOrgs(HttpServletRequest request) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        return Api.ok(Api.obj("items", Db.query(
                "SELECT orgs.id, orgs.name, orgs.created_at, members.role FROM orgs"
                        + " JOIN members ON members.org_id = orgs.id WHERE members.user_id = ? ORDER BY orgs.id",
                uid)));
    }

    @GetMapping("/api/orgs/{orgId}/members")
    public ResponseEntity<Object> listMembers(HttpServletRequest request, @PathVariable("orgId") String rawOrg) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        long orgId = Api.id(rawOrg);
        if (role(orgId, uid) == null) {
            return notFound();
        }
        return Api.ok(Api.obj("items", Db.query(
                "SELECT members.id, users.username, members.role FROM members"
                        + " JOIN users ON users.id = members.user_id WHERE members.org_id = ? ORDER BY members.id",
                orgId)));
    }

    @PostMapping("/api/orgs/{orgId}/members")
    public ResponseEntity<Object> addMember(HttpServletRequest request, @PathVariable("orgId") String rawOrg,
            @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        long orgId = Api.id(rawOrg);
        String mine = role(orgId, uid);
        if (mine == null) {
            return notFound();
        }
        Map<String, Object> body = Api.object(raw);
        if (body == null) {
            return Api.error(400, "JSON object expected");
        }
        if (!Api.bounded(body, "username", 50)) {
            return Api.error(400, "username must be 1-50 characters");
        }
        if (!(body.get("role") instanceof String role) || !ROLES.contains(role)) {
            return Api.error(400, "role must be owner, admin, member or viewer");
        }
        if (!manager(mine) || ("owner".equals(role) && !"owner".equals(mine))) {
            return Api.error(403, "your role does not allow this");
        }
        Map<String, Object> user = Db.one("SELECT id FROM users WHERE username = ?", body.get("username"));
        if (user == null) {
            return Api.error(404, "no such user");
        }
        try {
            long id = Db.update("INSERT INTO members (org_id, user_id, role) VALUES (?, ?, ?)", orgId, user.get("id"),
                    role);
            return Api.created(Api.obj("id", id, "username", body.get("username"), "role", role));
        } catch (Db.UniqueViolation e) {
            return Api.error(409, "already a member");
        }
    }

    private static Map<String, Object> target(long orgId, String username) {
        return Db.one("SELECT members.id, members.role, members.user_id, users.username FROM members"
                + " JOIN users ON users.id = members.user_id WHERE members.org_id = ? AND users.username = ?",
                orgId, username);
    }

    private static long owners(long orgId) {
        return Db.count("SELECT COUNT(*) FROM members WHERE org_id = ? AND role = 'owner'", orgId);
    }

    @PatchMapping("/api/orgs/{orgId}/members/{username}")
    public ResponseEntity<Object> updateMember(HttpServletRequest request, @PathVariable("orgId") String rawOrg,
            @PathVariable("username") String username, @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        long orgId = Api.id(rawOrg);
        String mine = role(orgId, uid);
        if (mine == null) {
            return notFound();
        }
        Map<String, Object> body = Api.object(raw);
        if (body == null || !(body.get("role") instanceof String role) || !ROLES.contains(role)) {
            return Api.error(400, "role must be owner, admin, member or viewer");
        }
        Map<String, Object> target = target(orgId, username);
        if (target == null) {
            return notFound();
        }
        String current = (String) target.get("role");
        if (!manager(mine) || (!"owner".equals(mine) && ("owner".equals(role) || "owner".equals(current)))) {
            return Api.error(403, "your role does not allow this");
        }
        if ("owner".equals(current) && !"owner".equals(role) && owners(orgId) == 1) {
            return Api.error(409, "an organization keeps at least one owner");
        }
        Db.update("UPDATE members SET role = ? WHERE id = ?", role, target.get("id"));
        return Api.ok(Api.obj("id", target.get("id"), "username", target.get("username"), "role", role));
    }

    @DeleteMapping("/api/orgs/{orgId}/members/{username}")
    public ResponseEntity<Object> removeMember(HttpServletRequest request, @PathVariable("orgId") String rawOrg,
            @PathVariable("username") String username) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        long orgId = Api.id(rawOrg);
        String mine = role(orgId, uid);
        if (mine == null) {
            return notFound();
        }
        Map<String, Object> target = target(orgId, username);
        if (target == null) {
            return notFound();
        }
        String current = (String) target.get("role");
        boolean own = ((Number) target.get("user_id")).longValue() == uid;
        if (!own && (!manager(mine) || (!"owner".equals(mine) && "owner".equals(current)))) {
            return Api.error(403, "your role does not allow this");
        }
        if ("owner".equals(current) && owners(orgId) == 1) {
            return Api.error(409, "an organization keeps at least one owner");
        }
        Db.update("DELETE FROM members WHERE id = ?", target.get("id"));
        return Api.noContent();
    }

    /** Validated project columns, in order; throws IllegalArgumentException with the message for a 400. */
    private static List<Object[]> fields(Map<String, Object> body, boolean partial) {
        if (body == null) {
            throw new IllegalArgumentException("JSON object expected");
        }
        List<Object[]> out = new ArrayList<>();
        if (body.containsKey("name") || !partial) {
            if (!Api.bounded(body, "name", 120)) {
                throw new IllegalArgumentException("name must be 1-120 characters");
            }
            out.add(new Object[] {"name", body.get("name")});
        }
        if (body.containsKey("description")) {
            Object value = body.get("description");
            if (value != null && (!(value instanceof String s) || Api.len(s) > 2000)) {
                throw new IllegalArgumentException("description must be at most 2000 characters");
            }
            out.add(new Object[] {"description", value});
        }
        if (body.containsKey("status")) {
            Object value = body.get("status");
            if (value != null && !(value instanceof String s && STATUSES.contains(s))) {
                throw new IllegalArgumentException("status must be active or archived");
            }
            out.add(new Object[] {"status", value == null ? "active" : value});
        }
        return out;
    }

    @GetMapping("/api/orgs/{orgId}/projects")
    public ResponseEntity<Object> listProjects(HttpServletRequest request, @PathVariable("orgId") String rawOrg) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        long orgId = Api.id(rawOrg);
        if (role(orgId, uid) == null) {
            return notFound();
        }
        String status = request.getParameter("status");
        if (status != null && !STATUSES.contains(status)) {
            return Api.error(400, "status must be active or archived");
        }
        List<Map<String, Object>> rows = status == null
                ? Db.query(PROJECT + " WHERE org_id = ? ORDER BY id DESC", orgId)
                : Db.query(PROJECT + " WHERE org_id = ? AND status = ? ORDER BY id DESC", orgId, status);
        return Api.ok(Api.obj("items", rows));
    }

    @PostMapping("/api/orgs/{orgId}/projects")
    public ResponseEntity<Object> createProject(HttpServletRequest request, @PathVariable("orgId") String rawOrg,
            @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        long orgId = Api.id(rawOrg);
        String mine = role(orgId, uid);
        if (mine == null) {
            return notFound();
        }
        Map<String, Object> values = Api.obj("name", null, "description", null, "status", "active");
        try {
            for (Object[] field : fields(Api.object(raw), false)) {
                values.put((String) field[0], field[1]);
            }
        } catch (IllegalArgumentException e) {
            return Api.error(400, e.getMessage());
        }
        if ("viewer".equals(mine)) {
            return Api.error(403, "viewers cannot create projects");
        }
        long id = Db.update("INSERT INTO projects (org_id, name, description, status, created_at) VALUES (?, ?, ?, ?, ?)",
                orgId, values.get("name"), values.get("description"), values.get("status"), Api.now());
        return Api.created(Db.one(PROJECT + " WHERE id = ?", id));
    }

    /** {project, role} visible to the caller, or null. */
    private static Object[] visible(String rawId, long uid) {
        Map<String, Object> row = Db.one(PROJECT + " WHERE id = ?", Api.id(rawId));
        if (row == null) {
            return null;
        }
        String role = role(((Number) row.get("org_id")).longValue(), uid);
        return role == null ? null : new Object[] {row, role};
    }

    @GetMapping("/api/projects/{id}")
    public ResponseEntity<Object> getProject(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        Object[] found = visible(rawId, uid);
        return found == null ? notFound() : Api.ok(found[0]);
    }

    @PatchMapping("/api/projects/{id}")
    @SuppressWarnings("unchecked")
    public ResponseEntity<Object> updateProject(HttpServletRequest request, @PathVariable("id") String rawId,
            @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        Object[] found = visible(rawId, uid);
        if (found == null) {
            return notFound();
        }
        List<Object[]> fields;
        try {
            fields = fields(Api.object(raw), true);
        } catch (IllegalArgumentException e) {
            return Api.error(400, e.getMessage());
        }
        if ("viewer".equals(found[1])) {
            return Api.error(403, "viewers cannot edit projects");
        }
        Object id = ((Map<String, Object>) found[0]).get("id");
        if (!fields.isEmpty()) {
            List<String> sets = new ArrayList<>();
            List<Object> args = new ArrayList<>();
            for (Object[] field : fields) {
                sets.add(field[0] + " = ?");
                args.add(field[1]);
            }
            args.add(id);
            Db.update("UPDATE projects SET " + String.join(", ", sets) + " WHERE id = ?", args.toArray());
        }
        return Api.ok(Db.one(PROJECT + " WHERE id = ?", id));
    }

    @DeleteMapping("/api/projects/{id}")
    @SuppressWarnings("unchecked")
    public ResponseEntity<Object> deleteProject(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return unauthenticated();
        }
        Object[] found = visible(rawId, uid);
        if (found == null) {
            return notFound();
        }
        if (!manager((String) found[1])) {
            return Api.error(403, "only owners and admins delete projects");
        }
        Db.update("DELETE FROM projects WHERE id = ?", ((Map<String, Object>) found[0]).get("id"));
        return Api.noContent();
    }
}

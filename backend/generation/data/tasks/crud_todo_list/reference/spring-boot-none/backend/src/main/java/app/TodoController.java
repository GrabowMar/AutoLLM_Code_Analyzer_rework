package app;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** Spring Boot reference for crud_todo_list (conformance kit, contract_bearer); a port of the Flask reference. */
@RestController
public class TodoController {

    static {
        Db.init("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
              description TEXT, priority TEXT NOT NULL DEFAULT 'medium', due_date TEXT,
              completed INTEGER NOT NULL DEFAULT 0, completed_at TEXT, created_at TEXT NOT NULL)
            """);
    }

    private static final Set<String> PRIORITIES = Set.of("low", "medium", "high");
    private static final Pattern DATE = Pattern.compile("^\\d{4}-\\d{2}-\\d{2}$");
    private static final String COLS = "id, title, description, priority, due_date, completed, completed_at, created_at";

    private static boolean validDate(Object value) {
        if (!(value instanceof String s) || !DATE.matcher(s).matches()) {
            return false;
        }
        try {
            LocalDate.parse(s);
            return true;
        } catch (DateTimeParseException e) {
            return false;
        }
    }

    private static String validate(Map<String, Object> body, boolean partial) {
        if (body == null) {
            return "JSON object expected";
        }
        if ((body.containsKey("title") || !partial) && !Api.bounded(body, "title", 255)) {
            return "title must be 1-255 characters";
        }
        Object description = body.get("description");
        if (description != null && (!(description instanceof String s) || Api.len(s) > 2000)) {
            return "description must be at most 2000 characters";
        }
        Object priority = body.get("priority");
        if (priority != null && !(priority instanceof String p && PRIORITIES.contains(p))) {
            return "priority must be low, medium or high";
        }
        if (body.get("due_date") != null && !validDate(body.get("due_date"))) {
            return "due_date must be YYYY-MM-DD";
        }
        if (body.get("completed") != null && !(body.get("completed") instanceof Boolean)) {
            return "completed must be a boolean";
        }
        return null;
    }

    private static Map<String, Object> json(Map<String, Object> row) {
        row.put("completed", ((Number) row.get("completed")).intValue() != 0);
        return row;
    }

    private static Map<String, Object> owned(long id, long uid) {
        Map<String, Object> row = Db.one("SELECT " + COLS + " FROM todos WHERE id = ? AND user_id = ?", id, uid);
        return row == null ? null : json(row);
    }

    @GetMapping("/api/todos")
    public ResponseEntity<Object> list(HttpServletRequest request) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        List<String> where = new ArrayList<>(List.of("user_id = ?"));
        List<Object> args = new ArrayList<>(List.of(uid));
        String completed = request.getParameter("completed");
        if (completed != null) {
            where.add("completed = ?");
            args.add(completed.equalsIgnoreCase("true") || completed.equals("1") ? 1 : 0);
        }
        String priority = request.getParameter("priority");
        if (priority != null) {
            if (!PRIORITIES.contains(priority)) {
                return Api.error(400, "invalid priority");
            }
            where.add("priority = ?");
            args.add(priority);
        }
        int page;
        int pageSize;
        try {
            page = Math.max(1, Integer.parseInt(param(request, "page", "1")));
            pageSize = Math.min(200, Math.max(1, Integer.parseInt(param(request, "page_size", "50"))));
        } catch (NumberFormatException e) {
            return Api.error(400, "page and page_size must be integers");
        }
        String clause = String.join(" AND ", where);
        long total = Db.count("SELECT COUNT(*) FROM todos WHERE " + clause, args.toArray());
        List<Object> pageArgs = new ArrayList<>(args);
        pageArgs.add(pageSize);
        pageArgs.add((page - 1) * pageSize);
        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> row : Db.query(
                "SELECT " + COLS + " FROM todos WHERE " + clause + " ORDER BY completed ASC, id DESC LIMIT ? OFFSET ?",
                pageArgs.toArray())) {
            items.add(json(row));
        }
        long remaining = Db.count("SELECT COUNT(*) FROM todos WHERE user_id = ? AND completed = 0", uid);
        return Api.ok(Api.obj("items", items, "total", total, "page", page, "page_size", pageSize,
                "remaining", remaining));
    }

    private static String param(HttpServletRequest request, String name, String fallback) {
        String value = request.getParameter(name);
        return value == null ? fallback : value;
    }

    @PostMapping("/api/todos")
    public ResponseEntity<Object> create(HttpServletRequest request, @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> body = Api.object(raw);
        String problem = validate(body, false);
        if (problem != null) {
            return Api.error(400, problem);
        }
        boolean done = Boolean.TRUE.equals(body.get("completed"));
        Object priority = body.get("priority") == null ? "medium" : body.get("priority");
        long id = Db.update(
                "INSERT INTO todos (user_id, title, description, priority, due_date, completed, completed_at, created_at)"
                        + " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                uid, body.get("title"), body.get("description"), priority, body.get("due_date"), done ? 1 : 0,
                done ? Api.now() : null, Api.now());
        return Api.created(owned(id, uid));
    }

    @GetMapping("/api/todos/{id}")
    public ResponseEntity<Object> get(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> todo = owned(Api.id(rawId), uid);
        return todo == null ? Api.error(404, "not found") : Api.ok(todo);
    }

    @PatchMapping("/api/todos/{id}")
    public ResponseEntity<Object> update(HttpServletRequest request, @PathVariable("id") String rawId,
            @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        long id = Api.id(rawId);
        Map<String, Object> current = owned(id, uid);
        if (current == null) {
            return Api.error(404, "not found");
        }
        Map<String, Object> body = Api.object(raw);
        String problem = validate(body, true);
        if (problem != null) {
            return Api.error(400, problem);
        }
        List<String> sets = new ArrayList<>();
        List<Object> args = new ArrayList<>();
        for (String key : List.of("title", "description", "priority", "due_date")) {
            if (body.containsKey(key)) {
                Object value = body.get(key);
                sets.add(key + " = ?");
                args.add(key.equals("priority") && value == null ? "medium" : value);
            }
        }
        if (body.get("completed") instanceof Boolean done) {
            sets.add("completed = ?");
            args.add(done ? 1 : 0);
            if (done && !Boolean.TRUE.equals(current.get("completed"))) {
                sets.add("completed_at = ?");
                args.add(Api.now());
            } else if (!done) {
                sets.add("completed_at = ?");
                args.add(null);
            }
        }
        if (!sets.isEmpty()) {
            args.add(id);
            Db.update("UPDATE todos SET " + String.join(", ", sets) + " WHERE id = ?", args.toArray());
        }
        return Api.ok(owned(id, uid));
    }

    @DeleteMapping("/api/todos/{id}")
    public ResponseEntity<Object> delete(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        long id = Api.id(rawId);
        if (owned(id, uid) == null) {
            return Api.error(404, "not found");
        }
        Db.update("DELETE FROM todos WHERE id = ?", id);
        return Api.noContent();
    }
}

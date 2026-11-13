package app;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/**
 * Spring Boot reference for notes_markdown_render (oracle reference, contract_bearer); a port of the Python reference
 * core. Errors are {"detail": "<message>"}; Markdown renders the bodies. seq orders the list by the latest change.
 */
@RestController
public class NoteController {

    static {
        Db.init("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
              body TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
              seq INTEGER NOT NULL DEFAULT 0)
            """);
    }

    private static final String NOTE = "SELECT notes.id, notes.title, notes.body, notes.published, notes.updated_at,"
            + " users.username AS author FROM notes JOIN users ON users.id = notes.user_id";

    private static Map<String, Object> summary(Map<String, Object> row) {
        return Api.obj("id", row.get("id"), "title", row.get("title"), "published",
                ((Number) row.get("published")).intValue() != 0, "updated_at", row.get("updated_at"));
    }

    private static Map<String, Object> full(Map<String, Object> row) {
        Map<String, Object> out = summary(row);
        out.put("body", row.get("body"));
        out.put("author", row.get("author"));
        return out;
    }

    /** Validated columns, in order; throws IllegalArgumentException with the message for a 400. */
    private static List<Object[]> fields(Map<String, Object> body, boolean partial) {
        if (body == null) {
            throw new IllegalArgumentException("JSON object expected");
        }
        List<Object[]> out = new ArrayList<>();
        if (body.containsKey("title") || !partial) {
            if (!Api.bounded(body, "title", 120)) {
                throw new IllegalArgumentException("title must be 1-120 characters");
            }
            out.add(new Object[] {"title", body.get("title")});
        }
        if (body.containsKey("body") || !partial) {
            if (!(body.get("body") instanceof String text) || text.isBlank() || Api.len(text) > 50000) {
                throw new IllegalArgumentException("body must be 1-50000 characters and not blank");
            }
            out.add(new Object[] {"body", text});
        }
        if (body.containsKey("published")) {
            Object value = body.get("published");
            if (value != null && !(value instanceof Boolean)) {
                throw new IllegalArgumentException("published must be true or false");
            }
            out.add(new Object[] {"published", Boolean.TRUE.equals(value) ? 1 : 0});
        }
        return out;
    }

    private static long nextSeq() {
        return Db.count("SELECT COALESCE(MAX(seq), 0) + 1 FROM notes");
    }

    private static Map<String, Object> owned(String rawId, long uid) {
        return Db.one(NOTE + " WHERE notes.id = ? AND notes.user_id = ?", Api.id(rawId), uid);
    }

    private static ResponseEntity<Object> html(String markdown) {
        return ResponseEntity.ok().contentType(new MediaType("text", "html", java.nio.charset.StandardCharsets.UTF_8))
                .body(Markdown.render(markdown));
    }

    @GetMapping("/api/notes")
    public ResponseEntity<Object> list(HttpServletRequest request) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> row : Db.query(NOTE + " WHERE notes.user_id = ? ORDER BY notes.seq DESC", uid)) {
            items.add(summary(row));
        }
        return Api.ok(Api.obj("items", items));
    }

    @PostMapping("/api/notes")
    public ResponseEntity<Object> create(HttpServletRequest request, @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> values = Api.obj("title", null, "body", null, "published", 0);
        try {
            for (Object[] field : fields(Api.object(raw), false)) {
                values.put((String) field[0], field[1]);
            }
        } catch (IllegalArgumentException e) {
            return Api.error(400, e.getMessage());
        }
        long id = Db.update("INSERT INTO notes (user_id, title, body, published, updated_at, seq) VALUES (?, ?, ?, ?, ?, ?)",
                uid, values.get("title"), values.get("body"), values.get("published"), Api.now(), nextSeq());
        return Api.created(full(Db.one(NOTE + " WHERE notes.id = ?", id)));
    }

    @GetMapping("/api/notes/{id}")
    public ResponseEntity<Object> get(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        return row == null ? Api.error(404, "not found") : Api.ok(full(row));
    }

    @PatchMapping("/api/notes/{id}")
    public ResponseEntity<Object> update(HttpServletRequest request, @PathVariable("id") String rawId,
            @RequestBody(required = false) String raw) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        if (row == null) {
            return Api.error(404, "not found");
        }
        List<Object[]> fields;
        try {
            fields = fields(Api.object(raw), true);
        } catch (IllegalArgumentException e) {
            return Api.error(400, e.getMessage());
        }
        fields.add(new Object[] {"updated_at", Api.now()});
        fields.add(new Object[] {"seq", nextSeq()});
        List<String> sets = new ArrayList<>();
        List<Object> args = new ArrayList<>();
        for (Object[] field : fields) {
            sets.add(field[0] + " = ?");
            args.add(field[1]);
        }
        args.add(row.get("id"));
        Db.update("UPDATE notes SET " + String.join(", ", sets) + " WHERE id = ?", args.toArray());
        return Api.ok(full(Db.one(NOTE + " WHERE notes.id = ?", row.get("id"))));
    }

    @DeleteMapping("/api/notes/{id}")
    public ResponseEntity<Object> delete(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        if (row == null) {
            return Api.error(404, "not found");
        }
        Db.update("DELETE FROM notes WHERE id = ?", row.get("id"));
        return Api.noContent();
    }

    @GetMapping("/api/notes/{id}/html")
    public ResponseEntity<Object> render(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        return row == null ? Api.error(404, "not found") : html((String) row.get("body"));
    }

    @GetMapping("/api/public/notes/{id}/html")
    public ResponseEntity<Object> renderPublic(@PathVariable("id") String rawId) {
        Map<String, Object> row = Db.one(NOTE + " WHERE notes.id = ? AND notes.published = 1", Api.id(rawId));
        return row == null ? Api.error(404, "not found") : html((String) row.get("body"));
    }
}

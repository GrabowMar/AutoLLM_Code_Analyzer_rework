package app;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Base64;
import java.util.List;
import java.util.Map;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;
import org.springframework.web.multipart.MultipartHttpServletRequest;
import org.springframework.web.util.WebUtils;

/**
 * Spring Boot reference for file_share_multipart (conformance kit, contract_bearer); a port of the Flask reference.
 *
 * <p>Uploads over 10 MiB answer 413 (application.properties, UploadLimits). Stored files are named by a random id
 * under data/files; the client's filename is metadata only.
 */
@RestController
public class FileController {

    static {
        Db.init("""
            CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, pw TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS files (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, stored TEXT NOT NULL,
              filename TEXT, description TEXT, size_bytes INTEGER, content_type TEXT, uploaded_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS shares (token TEXT PRIMARY KEY, file_id INTEGER NOT NULL)
            """);
    }

    private static final Path FILES = Path.of(Db.DATA_DIR, "files");
    private static final SecureRandom RANDOM = new SecureRandom();
    private static final String COLS =
            "files.id, files.stored, files.filename, files.description, files.size_bytes, files.content_type,"
                    + " files.uploaded_at";

    private static Map<String, Object> json(Map<String, Object> row) {
        return Api.obj("id", row.get("id"), "description", row.get("description"), "filename", row.get("filename"),
                "size_bytes", row.get("size_bytes"), "content_type", row.get("content_type"), "uploaded_at",
                row.get("uploaded_at"), "download_url", "/api/files/" + row.get("id") + "/download");
    }

    private static Map<String, Object> owned(String rawId, long uid) {
        return Db.one("SELECT " + COLS + " FROM files WHERE id = ? AND user_id = ?", Api.id(rawId), uid);
    }

    private static ResponseEntity<Object> send(Map<String, Object> row) {
        byte[] data;
        try {
            data = Files.readAllBytes(FILES.resolve((String) row.get("stored")));
        } catch (IOException e) {
            return Api.error(404, "not found");
        }
        String name = row.get("filename") instanceof String s && !s.isEmpty() ? s : "file";
        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .header(HttpHeaders.CONTENT_DISPOSITION,
                        "attachment; filename=\"" + name.replaceAll("[^A-Za-z0-9._-]", "_") + "\"")
                .header("X-Content-Type-Options", "nosniff")
                .body(data);
    }

    @PostMapping("/api/files")
    public ResponseEntity<Object> upload(HttpServletRequest request) throws IOException {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        MultipartHttpServletRequest multipart = WebUtils.getNativeRequest(request, MultipartHttpServletRequest.class);
        MultipartFile file = multipart == null ? null : multipart.getFile("file");
        if (file == null) {
            return Api.error(400, "file is required");
        }
        String description = multipart.getParameter("description");
        if (description != null && Api.len(description) > 300) {
            return Api.error(400, "description must be at most 300 characters");
        }
        byte[] data = file.getBytes();
        if (data.length == 0) {
            return Api.error(400, "the file is empty");
        }
        String name = file.getOriginalFilename() == null ? "" : file.getOriginalFilename();
        name = name.substring(name.lastIndexOf('/') + 1);
        if (name.isEmpty()) {
            name = "file";
        }
        String type = file.getContentType();
        if (type != null) {
            type = type.split(";", 2)[0].strip();
        }
        String stored = Auth.randomHex(16);
        Files.createDirectories(FILES);
        Files.write(FILES.resolve(stored), data);
        long id = Db.update(
                "INSERT INTO files (user_id, stored, filename, description, size_bytes, content_type, uploaded_at)"
                        + " VALUES (?, ?, ?, ?, ?, ?, ?)",
                uid, stored, name, description, data.length, type, Api.now());
        return Api.created(json(Db.one("SELECT " + COLS + " FROM files WHERE id = ?", id)));
    }

    @GetMapping("/api/files")
    public ResponseEntity<Object> list(HttpServletRequest request) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        int page;
        int pageSize;
        try {
            String rawPage = request.getParameter("page");
            String rawSize = request.getParameter("page_size");
            page = Math.max(1, rawPage == null ? 1 : Integer.parseInt(rawPage));
            pageSize = Math.min(100, Math.max(1, rawSize == null ? 20 : Integer.parseInt(rawSize)));
        } catch (NumberFormatException e) {
            return Api.error(400, "page and page_size must be integers");
        }
        long total = Db.count("SELECT COUNT(*) FROM files WHERE user_id = ?", uid);
        List<Map<String, Object>> items = new ArrayList<>();
        for (Map<String, Object> row : Db.query(
                "SELECT " + COLS + " FROM files WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
                uid, pageSize, (page - 1) * pageSize)) {
            items.add(json(row));
        }
        return Api.ok(Api.obj("items", items, "total", total, "page", page, "page_size", pageSize));
    }

    @GetMapping("/api/files/{id}")
    public ResponseEntity<Object> get(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        return row == null ? Api.error(404, "not found") : Api.ok(json(row));
    }

    @GetMapping("/api/files/{id}/download")
    public ResponseEntity<Object> download(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        return row == null ? Api.error(404, "not found") : send(row);
    }

    @DeleteMapping("/api/files/{id}")
    public ResponseEntity<Object> delete(HttpServletRequest request, @PathVariable("id") String rawId)
            throws IOException {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        if (row == null) {
            return Api.error(404, "not found");
        }
        Db.update("DELETE FROM shares WHERE file_id = ?", row.get("id"));
        Db.update("DELETE FROM files WHERE id = ?", row.get("id"));
        Files.deleteIfExists(FILES.resolve((String) row.get("stored")));
        return Api.noContent();
    }

    @PostMapping("/api/files/{id}/shares")
    public ResponseEntity<Object> share(HttpServletRequest request, @PathVariable("id") String rawId) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        if (row == null) {
            return Api.error(404, "not found");
        }
        byte[] buf = new byte[24];
        RANDOM.nextBytes(buf);
        String token = Base64.getUrlEncoder().withoutPadding().encodeToString(buf);
        Db.update("INSERT INTO shares (token, file_id) VALUES (?, ?)", token, row.get("id"));
        return Api.created(Api.obj("token", token, "url", "/api/shared/" + token));
    }

    @DeleteMapping("/api/files/{id}/shares/{token}")
    public ResponseEntity<Object> unshare(HttpServletRequest request, @PathVariable("id") String rawId,
            @PathVariable("token") String token) {
        long uid = Auth.user(request);
        if (uid < 0) {
            return Api.error(401, "authentication required");
        }
        Map<String, Object> row = owned(rawId, uid);
        if (row == null) {
            return Api.error(404, "not found");
        }
        long removed = Db.update("DELETE FROM shares WHERE token = ? AND file_id = ?", token, row.get("id"));
        return removed > 0 ? Api.noContent() : Api.error(404, "not found");
    }

    @GetMapping("/api/shared/{token}")
    public ResponseEntity<Object> shared(@PathVariable("token") String token) {
        Map<String, Object> row = Db.one(
                "SELECT " + COLS + " FROM shares JOIN files ON files.id = shares.file_id WHERE shares.token = ?",
                token);
        return row == null ? Api.error(404, "not found") : send(row);
    }
}

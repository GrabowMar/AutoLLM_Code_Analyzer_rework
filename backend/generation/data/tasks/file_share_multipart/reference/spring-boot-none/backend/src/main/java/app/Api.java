package app;

import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import tools.jackson.databind.json.JsonMapper;

/** JSON and validation helpers shared by the controllers. */
public final class Api {

    private static final JsonMapper JSON = JsonMapper.builder().build();
    private static final DateTimeFormatter STAMP =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss'Z'").withZone(ZoneOffset.UTC);

    private Api() {}

    /** The request body as a JSON object, or null when it is not one. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(String body) {
        if (body == null || body.isBlank()) {
            return null;
        }
        try {
            Object value = JSON.readValue(body, Object.class);
            return value instanceof Map ? (Map<String, Object>) value : null;
        } catch (RuntimeException e) {
            return null;
        }
    }

    public static Map<String, Object> obj(Object... kv) {
        Map<String, Object> out = new LinkedHashMap<>();
        for (int i = 0; i < kv.length; i += 2) {
            out.put((String) kv[i], kv[i + 1]);
        }
        return out;
    }

    public static ResponseEntity<Object> ok(Object payload) {
        return ResponseEntity.ok(payload);
    }

    public static ResponseEntity<Object> created(Object payload) {
        return ResponseEntity.status(HttpStatus.CREATED).body(payload);
    }

    public static ResponseEntity<Object> noContent() {
        return ResponseEntity.noContent().build();
    }

    public static ResponseEntity<Object> error(int status, String message) {
        return ResponseEntity.status(status).body(obj("error", message));
    }

    public static int len(String s) {
        return s.codePointCount(0, s.length());
    }

    /** body[key] is a string of 1..max characters. */
    public static boolean bounded(Map<String, Object> body, String key, int max) {
        return body.get(key) instanceof String s && len(s) >= 1 && len(s) <= max;
    }

    /** A canonical non-negative integer path id, or -1. */
    public static long id(String raw) {
        try {
            long value = Long.parseLong(raw);
            return value >= 0 && raw.equals(Long.toString(value)) ? value : -1;
        } catch (NumberFormatException e) {
            return -1;
        }
    }

    public static String now() {
        return STAMP.format(Instant.now());
    }
}

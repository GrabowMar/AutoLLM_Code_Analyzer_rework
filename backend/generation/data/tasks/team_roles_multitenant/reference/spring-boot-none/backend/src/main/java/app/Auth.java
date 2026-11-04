package app;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HexFormat;
import java.util.Map;

import javax.crypto.Mac;
import javax.crypto.SecretKeyFactory;
import javax.crypto.spec.PBEKeySpec;
import javax.crypto.spec.SecretKeySpec;

import jakarta.servlet.http.HttpServletRequest;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

/** Registration, login, PBKDF2 password hashes and HMAC-signed bearer tokens ("uid.signature"). */
@RestController
public class Auth {

    private static final SecureRandom RANDOM = new SecureRandom();
    private static final byte[] SECRET = secret();

    private static byte[] secret() {
        String key = System.getenv("SECRET_KEY");
        return (key == null || key.isEmpty() ? randomHex(32) : key).getBytes(StandardCharsets.UTF_8);
    }

    public static String randomHex(int bytes) {
        byte[] buf = new byte[bytes];
        RANDOM.nextBytes(buf);
        return HexFormat.of().formatHex(buf);
    }

    static String hash(String password, String salt) {
        String s = salt == null ? randomHex(16) : salt;
        try {
            PBEKeySpec spec = new PBEKeySpec(password.toCharArray(), s.getBytes(StandardCharsets.UTF_8), 100_000, 256);
            byte[] key = SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).getEncoded();
            return s + "$" + HexFormat.of().formatHex(key);
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    static String sign(long uid) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(SECRET, "HmacSHA256"));
            String raw = Long.toString(uid);
            return raw + "." + HexFormat.of().formatHex(mac.doFinal(raw.getBytes(StandardCharsets.UTF_8)));
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    private static boolean same(String a, String b) {
        return MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }

    /** The signed-in user's id, or -1. */
    public static long user(HttpServletRequest request) {
        String header = request.getHeader("Authorization");
        if (header == null || !header.startsWith("Bearer ")) {
            return -1;
        }
        String token = header.substring(7);
        int dot = token.indexOf('.');
        long uid = Api.id(dot < 0 ? token : token.substring(0, dot));
        if (uid <= 0 || !same(sign(uid), token)) {
            return -1;
        }
        return Db.one("SELECT id FROM users WHERE id = ?", uid) == null ? -1 : uid;
    }

    @PostMapping("/api/auth/register")
    public ResponseEntity<Object> register(@RequestBody(required = false) String raw) {
        Map<String, Object> body = Api.object(raw);
        if (body == null) {
            return Api.error(400, "JSON object expected");
        }
        if (!Api.bounded(body, "username", 50)) {
            return Api.error(400, "username must be 1-50 characters");
        }
        if (!(body.get("password") instanceof String password) || password.isEmpty()) {
            return Api.error(400, "password is required");
        }
        String username = (String) body.get("username");
        try {
            long id = Db.update("INSERT INTO users (username, pw) VALUES (?, ?)", username, hash(password, null));
            return Api.created(Api.obj("id", id, "username", username));
        } catch (Db.UniqueViolation e) {
            return Api.error(409, "username taken");
        }
    }

    @PostMapping("/api/auth/login")
    public ResponseEntity<Object> login(@RequestBody(required = false) String raw) {
        Map<String, Object> body = Api.object(raw);
        if (body == null || !(body.get("username") instanceof String username)
                || !(body.get("password") instanceof String password)) {
            return Api.error(401, "invalid credentials");
        }
        Map<String, Object> row = Db.one("SELECT id, pw FROM users WHERE username = ?", username);
        if (row == null) {
            return Api.error(401, "invalid credentials");
        }
        String stored = (String) row.get("pw");
        if (!same(hash(password, stored.split("\\$", 2)[0]), stored)) {
            return Api.error(401, "invalid credentials");
        }
        return Api.ok(Api.obj("token", sign(((Number) row.get("id")).longValue())));
    }
}

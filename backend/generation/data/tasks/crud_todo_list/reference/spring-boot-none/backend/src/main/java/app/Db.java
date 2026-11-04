package app;

import java.io.File;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.ResultSetMetaData;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.locks.ReentrantLock;

/** SQLite access for the kit reference: one statement at a time under a global lock (data/app.db). */
public final class Db {

    public static final String DATA_DIR = System.getenv().getOrDefault("DATA_DIR", "data");
    private static final String URL = "jdbc:sqlite:" + new File(DATA_DIR, "app.db").getPath();
    private static final ReentrantLock LOCK = new ReentrantLock();

    private Db() {}

    public static void init(String schema) {
        new File(DATA_DIR).mkdirs();
        LOCK.lock();
        try (Connection c = DriverManager.getConnection(URL); Statement st = c.createStatement()) {
            for (String sql : schema.split(";")) {
                if (!sql.isBlank()) {
                    st.executeUpdate(sql);
                }
            }
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        } finally {
            LOCK.unlock();
        }
    }

    private static void bind(PreparedStatement ps, Object... args) throws SQLException {
        for (int i = 0; i < args.length; i++) {
            ps.setObject(i + 1, args[i]);
        }
    }

    public static List<Map<String, Object>> query(String sql, Object... args) {
        LOCK.lock();
        try (Connection c = DriverManager.getConnection(URL); PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, args);
            List<Map<String, Object>> out = new ArrayList<>();
            try (ResultSet rs = ps.executeQuery()) {
                ResultSetMetaData meta = rs.getMetaData();
                while (rs.next()) {
                    Map<String, Object> row = new LinkedHashMap<>();
                    for (int i = 1; i <= meta.getColumnCount(); i++) {
                        row.put(meta.getColumnLabel(i), rs.getObject(i));
                    }
                    out.add(row);
                }
            }
            return out;
        } catch (SQLException e) {
            throw new IllegalStateException(e);
        } finally {
            LOCK.unlock();
        }
    }

    public static Map<String, Object> one(String sql, Object... args) {
        List<Map<String, Object>> rows = query(sql, args);
        return rows.isEmpty() ? null : rows.get(0);
    }

    public static long count(String sql, Object... args) {
        return ((Number) one(sql, args).values().iterator().next()).longValue();
    }

    /** Runs an INSERT/UPDATE/DELETE; returns the new row id for an INSERT, else the affected row count. */
    public static long update(String sql, Object... args) {
        LOCK.lock();
        try (Connection c = DriverManager.getConnection(URL); PreparedStatement ps = c.prepareStatement(sql)) {
            bind(ps, args);
            int changed = ps.executeUpdate();
            if (!sql.stripLeading().toUpperCase().startsWith("INSERT")) {
                return changed;
            }
            try (Statement st = c.createStatement(); ResultSet rs = st.executeQuery("SELECT last_insert_rowid()")) {
                rs.next();
                return rs.getLong(1);
            }
        } catch (SQLException e) {
            if (e.getMessage() != null && e.getMessage().contains("UNIQUE")) {
                throw new UniqueViolation();
            }
            throw new IllegalStateException(e);
        } finally {
            LOCK.unlock();
        }
    }

    /** A UNIQUE constraint failed. */
    public static final class UniqueViolation extends RuntimeException {
        private static final long serialVersionUID = 1L;
    }
}

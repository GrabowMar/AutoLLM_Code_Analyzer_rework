class UserDao {
    ResultSet find(Statement stmt, String name) throws SQLException {
        return stmt.executeQuery("SELECT * FROM users WHERE name = '" + name + "'");
    }
}

class UserDao {
    ResultSet find(Connection conn, String name) throws SQLException {
        String sql = "SELECT id, name " + "FROM users WHERE name = ?";
        PreparedStatement ps = conn.prepareStatement(sql);
        ps.setString(1, name);
        return ps.executeQuery();
    }
}

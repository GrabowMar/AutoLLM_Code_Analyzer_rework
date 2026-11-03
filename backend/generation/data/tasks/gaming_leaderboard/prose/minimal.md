Build a game leaderboard. Signed-in players submit scores; anyone sees players ranked by their best score for all time, the last 7 days or today, and can look up one player's rank. The admin hides implausible scores.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/leaderboard, POST /api/scores, GET /api/scores, GET /api/admin/scores, POST /api/admin/scores/{id}/toggle.

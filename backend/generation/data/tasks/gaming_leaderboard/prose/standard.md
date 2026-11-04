Build a game leaderboard with a JSON API under `/api` and a web frontend. Signed-in players submit scores; anyone sees players ranked by their best score, all-time, this week or today. The admin hides implausible scores.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/leaderboard` ranks players by their best visible score in a period; each player appears once (query parameters: `period`, `limit`, `player`).
- `POST /api/scores` stores a score for the signed-in player and returns it with the player's all-time `rank` and `personal_best` (true when it is now their best).
- `GET /api/scores` lists the signed-in player's submissions, newest first, with `best_score`.
- `GET /api/admin/scores` lists every score, newest first, for the admin (query parameters: `page`, `page_size`).
- `POST /api/admin/scores/{id}/toggle` hides a score from the leaderboard or shows it again, for the admin.

Data:

- A score is returned as an object with `id`, `score`, `achieved_at`, `is_hidden`, `player`.
- `GET /api/leaderboard` returns `{"items": [...], "total": n}`; each item in `items` is an object with `rank`, `player`, `score` and `achieved_at`.
- `POST /api/scores` returns the score with `rank` and `personal_best` as well.
- `GET /api/scores` returns `{"items": [...], "best_score": ...}`; each item in `items` is a score.
- `GET /api/admin/scores` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a score.

Rules:

- A score always belongs to the signed-in player who submits it.
- Hidden scores are left out of the leaderboard, ranks and best scores.
- `achieved_at` is set by the server.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A leaderboard table (rank, player, score, date) with the top three marked and the signed-in player's row highlighted, period tabs, a player search and a refresh button.
- A score form that shows the new rank and a 'New personal best!' message.
- The player's recent submissions and best score.
- For the admin: every score with a hide/show button.

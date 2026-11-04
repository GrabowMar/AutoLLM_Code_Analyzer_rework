Build a server status board with a JSON API under `/api` and a web frontend. Check results are reported to the app (by a monitoring script or a person); the app itself never connects to the registered hosts. Anyone can read the board; signed-in users register servers and report checks.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/servers` lists servers sorted by name with their current status and `uptime_percent` (query parameters: `status`).
- `POST /api/servers` registers a server.
- `GET /api/servers/{id}` returns one server.
- `PUT /api/servers/{id}` replaces a server's `name` and `host`; status and history are kept.
- `DELETE /api/servers/{id}` deletes a server and its check history.
- `GET /api/servers/{id}/checks` lists a server's checks, newest first, with `total`, the number of all its checks (query parameters: `limit`).
- `POST /api/servers/{id}/checks` records a check result and sets the server's status and `last_check` from it.

Data:

- A server is returned as an object with `id`, `name`, `host`, `status`, `last_check`, `uptime_percent`.
- A check result is returned as an object with `id`, `server_id`, `status`, `response_ms`, `checked_at`.
- `GET /api/servers` returns `{"items": [...]}`; each item in `items` is a server.
- `GET /api/servers/{id}/checks` returns `{"items": [...], "total": n}`; each item in `items` is a check result.

Rules:

- A server without checks has status `unknown` and `last_check` `null`.
- `uptime_percent` is the share of the server's last 100 checks that were online, rounded to one decimal, or `null` without checks.
- The app never connects to, pings or resolves the registered hosts.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A status board with a coloured badge per server (online, offline, unknown), its host, last check time and uptime, and a status filter; it refreshes every 30 seconds.
- A server page with its check history and a chart of response times.
- For signed-in users: register, edit and delete forms (delete with confirmation) and a form to report a check.

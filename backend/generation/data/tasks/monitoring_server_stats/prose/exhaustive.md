Build a server status board with a JSON API under `/api` and a web frontend. Check results are reported to the app (by a monitoring script or a person); the app itself never connects to the registered hosts. Anyone can read the board; signed-in users register servers and report checks.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/servers` lists servers sorted by name with their current status and `uptime_percent`. `status` keeps servers with that current status.
- `POST /api/servers` registers a server. On success it answers 201. It answers 400 for invalid input.
- `GET /api/servers/{id}` returns one server. It answers 404 for an unknown id.
- `PUT /api/servers/{id}` replaces a server's `name` and `host`; status and history are kept. It answers 400 for invalid input; 404 for an unknown id.
- `DELETE /api/servers/{id}` deletes a server and its check history. On success it answers 204. It answers 404 for an unknown id.
- `GET /api/servers/{id}/checks` lists a server's checks, newest first, with `total`, the number of all its checks. `limit` caps the checks returned (default 50, at most 500). It answers 404 for an unknown id.
- `POST /api/servers/{id}/checks` records a check result and sets the server's status and `last_check` from it. On success it answers 201. It answers 400 for invalid input; 404 for an unknown id.

Data:

- A server is returned as an object with `id`, `name`, `host`, `status`, `last_check`, `uptime_percent`.
- A check result is returned as an object with `id`, `server_id`, `status`, `response_ms`, `checked_at`.
- `GET /api/servers` returns `{"items": [...]}`; each item in `items` is a server.
- `GET /api/servers/{id}/checks` returns `{"items": [...], "total": n}`; each item in `items` is a check result.

Validation (invalid input answers 400 and changes nothing):

- Server: `name` required, at most 100 characters; `host` required, a host name or IP address of at most 255 characters, without scheme, port or path.
- Check result: `status` required, one of `online`, `offline`; `response_ms` optional, a whole number of at least 0.

Rules:

- A server without checks has status `unknown` and `last_check` `null`.
- `uptime_percent` is the share of the server's last 100 checks that were online, rounded to one decimal, or `null` without checks.
- The app never connects to, pings or resolves the registered hosts.

Edge cases:

- A host with a scheme, port or path (for example `http://example.com` or `example.com:80`) answers 400.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A status board with a coloured badge per server (online, offline, unknown), its host, last check time and uptime, and a status filter; it refreshes every 30 seconds.
- A server page with its check history and a chart of response times.
- For signed-in users: register, edit and delete forms (delete with confirmation) and a form to report a check.

Build an incident center with a JSON API under `/api` and a web frontend for an operations team. Signed-in team members open incidents, update their status and post timeline events; every incident has a resolution target that depends on its severity. A public page shows aggregate figures only.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/incidents` lists incidents, newest opened first, as a paged list. `severity` keeps one severity; `status` keeps one status; `open=true` keeps unresolved incidents; `q` matches title, service or impact summary case-insensitively as a substring; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/incidents` opens an incident in status `investigating`. On success it answers 201. It answers 400 for invalid input.
- `GET /api/incidents/{id}` returns one incident. It answers 404 for an unknown id.
- `PATCH /api/incidents/{id}` changes only the fields it is given; a status change also appends a `status_change` event naming the old and new status. Statuses may move in any direction; moving a resolved incident to another status reopens it, clears `resolved_at` and adds 1 to `reopen_count`. It answers 400 for invalid input; 404 for an unknown id.
- `GET /api/incidents/{id}/timeline` lists an incident's timeline events, oldest first. It answers 404 for an unknown id.
- `POST /api/incidents/{id}/timeline` appends an event authored by the signed-in user; it does not change the incident's status. On success it answers 201. It answers 400 for invalid input; 404 for an unknown id.
- `GET /api/public/stats` returns `open_total`, `open_by_severity` (a count for each of `sev1` to `sev4`), `sla_breached_open` and `resolved_last_30_days`.

Data:

- An incident is returned as an object with `id`, `title`, `service`, `severity`, `status`, `commander`, `impact_summary`, `opened_at`, `resolved_at`, `reopen_count`, `opened_by`, `sla_due_at`, `sla_breached`.
- A timeline event is returned as an object with `id`, `type`, `message`, `created_at`, `author`.
- `GET /api/incidents` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an incident.
- `GET /api/incidents/{id}/timeline` returns `{"items": [...]}`; each item in `items` is a timeline event.

Validation (invalid input answers 400 and changes nothing):

- Incident: `title` required, at most 200 characters; `service` required, at most 100 characters; `severity` required, one of `sev1`, `sev2`, `sev3`, `sev4`; `status` optional, one of `investigating`, `mitigated`, `monitoring`, `resolved`, default `investigating`; `commander` optional, at most 50 characters (a responder's name); default the opening user's username; `impact_summary` optional, at most 2000 characters.
- Timeline event: `type` required, one of `acknowledgement`, `mitigation`, `escalation`, `recovery`, `customer_update`, `note` (`status_change` events are created only by the server); `message` required, at most 2000 characters.

Rules:

- Every signed-in team member may open and update every incident; incidents are never deleted.
- The resolution target is 60 minutes for `sev1`, 240 for `sev2`, 1440 for `sev3` and 4320 for `sev4`: `sla_due_at` is `opened_at` plus the target of the current severity, and `sla_breached` is true when the incident was resolved after it or is still unresolved past it.
- `resolved_at` is set when an incident becomes resolved.

Edge cases:

- Clients cannot post `status_change` events (400).

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A public page with the open counts per severity and the number of open incidents past their target.
- An incident list with severity and status filters, an 'open only' switch, a search box and paging; breached incidents are flagged.
- An incident page with its fields, status buttons, the timeline and a form to post an event.
- An 'Open incident' form showing the API's validation messages.

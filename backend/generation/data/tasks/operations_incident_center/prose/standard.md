Build an incident center with a JSON API under `/api` and a web frontend for an operations team. Signed-in team members open incidents, update their status and post timeline events; every incident has a resolution target that depends on its severity. A public page shows aggregate figures only.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/incidents` lists incidents, newest opened first, as a paged list (query parameters: `severity`, `status`, `open`, `q`, `page`, `page_size`).
- `POST /api/incidents` opens an incident in status `investigating`.
- `GET /api/incidents/{id}` returns one incident.
- `PATCH /api/incidents/{id}` changes only the fields it is given; a status change also appends a `status_change` event naming the old and new status.
- `GET /api/incidents/{id}/timeline` lists an incident's timeline events, oldest first.
- `POST /api/incidents/{id}/timeline` appends an event authored by the signed-in user; it does not change the incident's status.
- `GET /api/public/stats` returns `open_total`, `open_by_severity` (a count for each of `sev1` to `sev4`), `sla_breached_open` and `resolved_last_30_days`.

Data:

- An incident is returned as an object with `id`, `title`, `service`, `severity`, `status`, `commander`, `impact_summary`, `opened_at`, `resolved_at`, `reopen_count`, `opened_by`, `sla_due_at`, `sla_breached`.
- A timeline event is returned as an object with `id`, `type`, `message`, `created_at`, `author`.
- `GET /api/incidents` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an incident.
- `GET /api/incidents/{id}/timeline` returns `{"items": [...]}`; each item in `items` is a timeline event.

Rules:

- Every signed-in team member may open and update every incident; incidents are never deleted.
- The resolution target is 60 minutes for `sev1`, 240 for `sev2`, 1440 for `sev3` and 4320 for `sev4`: `sla_due_at` is `opened_at` plus the target of the current severity, and `sla_breached` is true when the incident was resolved after it or is still unresolved past it.
- `resolved_at` is set when an incident becomes resolved.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A public page with the open counts per severity and the number of open incidents past their target.
- An incident list with severity and status filters, an 'open only' switch, a search box and paging; breached incidents are flagged.
- An incident page with its fields, status buttons, the timeline and a form to post an event.
- An 'Open incident' form showing the API's validation messages.

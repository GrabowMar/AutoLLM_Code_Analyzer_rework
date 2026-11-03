Build a shared event calendar with a JSON API under `/api` and a web frontend. Anyone can browse events by day, week or month; signed-in users create, edit and delete events.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/events` lists events sorted by start time, optionally within a date range. `start_date` and `end_date` (`YYYY-MM-DD`, both inclusive, either may be omitted) keep events whose start falls on a UTC date in that range; a `start_date` after `end_date` answers 400; `end_date` is inclusive. It answers 400 for invalid input.
- `POST /api/events` creates an event. On success it answers 201. It answers 400 for invalid input.
- `GET /api/events/{id}` returns one event. It answers 404 for an unknown id.
- `PUT /api/events/{id}` replaces an event's fields; an omitted optional field is cleared. It answers 400 for invalid input; 404 for an unknown id.
- `DELETE /api/events/{id}` deletes an event. On success it answers 204. It answers 404 for an unknown id.

Data:

- An event is returned as an object with `id`, `title`, `description`, `location`, `start_time`, `end_time`, `created_at`, `created_by`.
- `GET /api/events` returns `{"items": [...], "total": n}`; each item in `items` is an event.

Validation (invalid input answers 400 and changes nothing):

- Event: `title` required, at most 200 characters; `description` optional, at most 2000 characters; `location` optional, at most 200 characters; `start_time` required, an ISO 8601 date-time; `end_time` optional, an ISO 8601 date-time not before `start_time` (equal is allowed).

Rules:

- Any signed-in user may change any event; `created_by` records the creator's username.
- Date-times without a UTC offset are taken as UTC; responses give every date-time in UTC with a trailing `Z`.

Edge cases:

- An event ending exactly when it starts is allowed.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Month, week and day views with navigation to the previous and next period and a 'Today' button.
- Clicking an event shows its details.
- For signed-in users: create and edit forms with date-time pickers that show the API's validation messages; delete with confirmation.

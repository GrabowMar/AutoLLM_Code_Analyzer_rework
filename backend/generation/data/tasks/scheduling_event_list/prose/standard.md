Build a shared event calendar with a JSON API under `/api` and a web frontend. Anyone can browse events by day, week or month; signed-in users create, edit and delete events.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/events` lists events sorted by start time, optionally within a date range (query parameters: `start_date`, `end_date`).
- `POST /api/events` creates an event.
- `GET /api/events/{id}` returns one event.
- `PUT /api/events/{id}` replaces an event's fields; an omitted optional field is cleared.
- `DELETE /api/events/{id}` deletes an event.

Data:

- An event is returned as an object with `id`, `title`, `description`, `location`, `start_time`, `end_time`, `created_at`, `created_by`.
- `GET /api/events` returns `{"items": [...], "total": n}`; each item in `items` is an event.

Rules:

- Any signed-in user may change any event; `created_by` records the creator's username.
- Date-times without a UTC offset are taken as UTC; responses give every date-time in UTC with a trailing `Z`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Month, week and day views with navigation to the previous and next period and a 'Today' button.
- Clicking an event shows its details.
- For signed-in users: create and edit forms with date-time pickers that show the API's validation messages; delete with confirmation.

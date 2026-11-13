Build a clinic appointment app with a JSON API under `/api` and a web frontend. Signed-in users book, reschedule and cancel the appointments they booked. Anyone can see how busy a day is, without patient details. The admin (clinic staff) sees every appointment.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/appointments` lists the user's appointments by date and time, earliest first, as a paged list. `status` keeps one status; `start_date` and `end_date` keep items dated within that inclusive range (an `end_date` before `start_date` answers 400); `end_date` is inclusive; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100). It answers 400 for invalid input.
- `POST /api/appointments` books an appointment in status `scheduled`. On success it answers 201. It answers 400 for invalid input.
- `GET /api/appointments/{id}` returns one appointment. It answers 404 for an unknown appointment or one of another user.
- `PATCH /api/appointments/{id}` changes any of `patient_name`, `date`, `time`, `reason` and `status`; cancelling is a change to status `cancelled`. It answers 400 for invalid input; 404 for an unknown appointment or one of another user; 409 for any change to a cancelled appointment.
- `DELETE /api/appointments/{id}` deletes an appointment. On success it answers 204. It answers 404 for an unknown appointment or one of another user.
- `GET /api/schedule` lists, for one day, each time that has scheduled appointments with the number booked, earliest first, and no patient details. `date` is the day to show (default today).
- `GET /api/admin/appointments` lists every appointment of every user, for the admin. `date` keeps one day; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).

Data:

- An appointment is returned as an object with `id`, `patient_name`, `date`, `time`, `reason`, `status`, `created_at`.
- `GET /api/appointments` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an appointment.
- `GET /api/schedule` returns `{"items": [...]}`; each item in `items` is an object with `time` and `booked`.
- `GET /api/admin/appointments` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an appointment.

Validation (invalid input answers 400 and changes nothing):

- Appointment: `patient_name` required, at most 100 characters; `date` required, a date `YYYY-MM-DD`; `time` required, a time `HH:MM` (24-hour); `reason` optional, at most 500 characters; `status` optional, one of `scheduled`, `completed`, `cancelled`, default `scheduled`.

Rules:

- Each user sees and changes only the appointments they booked; another user's appointment behaves as if it did not exist.
- Several appointments may share a date and time; there is no per-slot limit.
- Cancelled appointments do not count in the schedule.

Edge cases:

- A cancelled appointment cannot be changed again (409).

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A public date picker with the booked times of that day and how many appointments each has.
- The user's appointments with a status badge, status and date-range filters and paging; 'Mark completed' and 'Cancel' (with confirmation) on scheduled ones; cancelled ones greyed out.
- A booking form and a reschedule form showing the API's validation messages.
- For the admin: every appointment of the chosen day.

Build a clinic appointment app with a JSON API under `/api` and a web frontend. Signed-in users book, reschedule and cancel the appointments they booked. Anyone can see how busy a day is, without patient details. The admin (clinic staff) sees every appointment.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/appointments` lists the user's appointments by date and time, earliest first, as a paged list (query parameters: `status`, `start_date`, `end_date`, `page`, `page_size`).
- `POST /api/appointments` books an appointment in status `scheduled`.
- `GET /api/appointments/{id}` returns one appointment.
- `PATCH /api/appointments/{id}` changes any of `patient_name`, `date`, `time`, `reason` and `status`; cancelling is a change to status `cancelled`.
- `DELETE /api/appointments/{id}` deletes an appointment.
- `GET /api/schedule` lists, for one day, each time that has scheduled appointments with the number booked, earliest first, and no patient details (query parameters: `date`).
- `GET /api/admin/appointments` lists every appointment of every user, for the admin (query parameters: `date`, `page`, `page_size`).

Data:

- An appointment is returned as an object with `id`, `patient_name`, `date`, `time`, `reason`, `status`, `created_at`.
- `GET /api/appointments` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an appointment.
- `GET /api/schedule` returns `{"items": [...]}`; each item in `items` is an object with `time` and `booked`.
- `GET /api/admin/appointments` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an appointment.

Rules:

- Each user sees and changes only the appointments they booked; another user's appointment behaves as if it did not exist.
- Several appointments may share a date and time; there is no per-slot limit.
- Cancelled appointments do not count in the schedule.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A public date picker with the booked times of that day and how many appointments each has.
- The user's appointments with a status badge, status and date-range filters and paging; 'Mark completed' and 'Cancel' (with confirmation) on scheduled ones; cancelled ones greyed out.
- A booking form and a reschedule form showing the API's validation messages.
- For the admin: every appointment of the chosen day.

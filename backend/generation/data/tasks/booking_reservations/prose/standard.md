Build a restaurant reservation app with a JSON API under `/api` and a web frontend. Every time slot (a date plus a time) seats at most 20 guests. Anyone can see how full the slots of a day are; signed-in users book, change and cancel their own reservations; the admin sees every reservation.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/reservations` books a slot for the signed-in user.
- `GET /api/reservations` lists the signed-in user's reservations, earliest first (query parameters: `date`).
- `GET /api/reservations/{id}` returns one reservation.
- `PUT /api/reservations/{id}` replaces a reservation's fields; an omitted `notes` is cleared.
- `DELETE /api/reservations/{id}` cancels a reservation and frees its seats.
- `GET /api/availability` lists, for one day, every slot with at least one reservation, earliest first, with `booked` and `remaining` seats (query parameters: `date`).
- `GET /api/admin/reservations` lists every reservation of every user, for the admin (query parameters: `date`, `page`, `page_size`).

Data:

- A reservation is returned as an object with `id`, `name`, `email`, `date`, `time`, `party_size`, `notes`, `created_at`.
- `GET /api/reservations` returns `{"items": [...], "total": n}`; each item in `items` is a reservation.
- `GET /api/availability` returns `{"items": [...]}`; each item in `items` is an object with `time`, `booked` and `remaining`.
- `GET /api/admin/reservations` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a reservation.

Rules:

- Each user sees and changes only their own reservations; another user's reservation behaves as if it did not exist.
- A slot holds at most 20 guests over all its reservations; a booking or change that would exceed that is refused and changes nothing.
- When a reservation is replaced, its own guests are not counted twice.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A day picker showing that day's booked slots and remaining seats.
- A booking form (name, email, date, time, party size, notes) that shows the API's messages, including a full slot.
- The user's reservations grouped by date, with edit and cancel actions; cancel asks for confirmation.
- For the admin: every reservation of the chosen day with the guest total.

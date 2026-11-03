Build a restaurant reservation app with a JSON API under `/api` and a web frontend. Every time slot (a date plus a time) seats at most 20 guests. Anyone can see how full the slots of a day are; signed-in users book, change and cancel their own reservations; the admin sees every reservation.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/reservations` books a slot for the signed-in user. On success it answers 201. It answers 400 for invalid input; 409 when the slot would exceed 20 guests.
- `GET /api/reservations` lists the signed-in user's reservations, earliest first. `date` keeps one day.
- `GET /api/reservations/{id}` returns one reservation. It answers 404 for an unknown reservation or one of another user.
- `PUT /api/reservations/{id}` replaces a reservation's fields; an omitted `notes` is cleared. It answers 400 for invalid input; 404 for an unknown reservation or one of another user; 409 when the new slot would exceed 20 guests.
- `DELETE /api/reservations/{id}` cancels a reservation and frees its seats. On success it answers 204. It answers 404 for an unknown reservation or one of another user.
- `GET /api/availability` lists, for one day, every slot with at least one reservation, earliest first, with `booked` and `remaining` seats. `date` is the day to show (default today).
- `GET /api/admin/reservations` lists every reservation of every user, for the admin. `date` keeps one day; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).

Data:

- A reservation is returned as an object with `id`, `name`, `email`, `date`, `time`, `party_size`, `notes`, `created_at`.
- `GET /api/reservations` returns `{"items": [...], "total": n}`; each item in `items` is a reservation.
- `GET /api/availability` returns `{"items": [...]}`; each item in `items` is an object with `time`, `booked` and `remaining`.
- `GET /api/admin/reservations` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a reservation.

Validation (invalid input answers 400 and changes nothing):

- Reservation: `name` required, at most 100 characters; `email` required, an email address, at most 254 characters; `date` required, a date `YYYY-MM-DD`, today or later; `time` required, a time `HH:MM` (24-hour); `party_size` required, a whole number from 1 to 12; `notes` optional, at most 500 characters.

Rules:

- Each user sees and changes only their own reservations; another user's reservation behaves as if it did not exist.
- A slot holds at most 20 guests over all its reservations; a booking or change that would exceed that is refused and changes nothing.
- When a reservation is replaced, its own guests are not counted twice.

Edge cases:

- Two concurrent bookings for the last seats of a slot must not both succeed.
- A date in the past answers 400; 'today' is the server's date.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A day picker showing that day's booked slots and remaining seats.
- A booking form (name, email, date, time, party size, notes) that shows the API's messages, including a full slot.
- The user's reservations grouped by date, with edit and cancel actions; cancel asks for confirmation.
- For the admin: every reservation of the chosen day with the guest total.

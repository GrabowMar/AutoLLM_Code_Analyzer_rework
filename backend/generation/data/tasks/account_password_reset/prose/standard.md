Build an account service with password reset, with a JSON API under `/api` and a web frontend. Accounts have an email address; a user who forgot their password asks for a reset, receives a reset link by email and sets a new password. The app sends no real email: every outgoing message is stored in an outbox that stands in for a mail server.

API:

- `POST /api/auth/register` creates an account from a `username`, a `password` and an `email` address; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/me` returns the signed-in account's `id`, `username` and `email`.
- `PATCH /api/me` changes the account's `email`.
- `POST /api/me/password` changes the password; the body holds `current_password` and `new_password`.
- `POST /api/auth/password-reset` takes a `username` or an `email`; when it matches an account with an email address, a message with a single-use reset link is put in the outbox for that address.
- `POST /api/auth/password-reset/confirm` takes the `token` and a `new_password`, sets the password and invalidates the token.
- `GET /api/outbox` lists the outbox messages sent to the address given in `to`, newest first; this stands in for reading a mailbox (query parameters: `to`).

Data:

- An outbox message is returned as an object with `id`, `to`, `subject`, `body`, `sent_at`.
- `GET /api/outbox` returns `{"items": [...]}`; each item in `items` is an outbox message.

Rules:

- Passwords are at least 8 characters long and are never returned by any endpoint.
- Email addresses are unique ignoring case; an account may have none, and such an account cannot reset its password.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A 'Forgot password?' form that always answers 'If the account exists, a reset link is on its way'.
- A `/reset` page that reads the token from the link and asks for the new password twice.
- An account page with the email address, an edit form and a change-password form.
- A simple 'Mailbox' page that shows the outbox messages for an address, standing in for an email client.

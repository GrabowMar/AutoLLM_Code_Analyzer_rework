Build an account service with password reset, with a JSON API under `/api` and a web frontend. Accounts have an email address; a user who forgot their password asks for a reset, receives a reset link by email and sets a new password. The app sends no real email: every outgoing message is stored in an outbox that stands in for a mail server.

API:

- `POST /api/auth/register` creates an account from a `username`, a `password` and an `email` address; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/auth/register`: `username` is required, unique and at most 50 characters; `password` is required and at least 8 characters long. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login`: It answers 401 for a wrong username or password, without saying which of the two was wrong.
- `GET /api/me` returns the signed-in account's `id`, `username` and `email`.
- `PATCH /api/me` changes the account's `email`. It answers 400 for invalid input; 409 for an email another account uses.
- `POST /api/me/password` changes the password; the body holds `current_password` and `new_password`. On success it answers 204. It answers 400 for invalid input; 403 for a wrong current password.
- `POST /api/auth/password-reset` takes a `username` or an `email`; when it matches an account with an email address, a message with a single-use reset link is put in the outbox for that address. The response is the same 202 whether or not an account matched, so it does not reveal which accounts exist. The link has the form `/reset?token=<token>`. On success it answers 202. It answers 400 for a body with neither field.
- `POST /api/auth/password-reset/confirm` takes the `token` and a `new_password`, sets the password and invalidates the token. A token is valid for 30 minutes and only once; requesting a new reset invalidates earlier tokens of that account. Existing sign-ins of the account stop working. On success it answers 204. It answers 400 for an unknown, used or expired token, or a password that is too short.
- `GET /api/outbox` lists the outbox messages sent to the address given in `to`, newest first; this stands in for reading a mailbox. `to` is the recipient address (required). It answers 400 without `to`.

Data:

- An outbox message is returned as an object with `id`, `to`, `subject`, `body`, `sent_at`.
- `GET /api/outbox` returns `{"items": [...]}`; each item in `items` is an outbox message.

Rules:

- Passwords are at least 8 characters long and are never returned by any endpoint.
- Email addresses are unique ignoring case; an account may have none, and such an account cannot reset its password.

Edge cases:

- Requesting a reset for an unknown username or email answers exactly like a known one.
- A token from an account's earlier reset request no longer works once a newer one exists.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A 'Forgot password?' form that always answers 'If the account exists, a reset link is on its way'.
- A `/reset` page that reads the token from the link and asks for the new password twice.
- An account page with the email address, an edit form and a change-password form.
- A simple 'Mailbox' page that shows the outbox messages for an address, standing in for an email client.

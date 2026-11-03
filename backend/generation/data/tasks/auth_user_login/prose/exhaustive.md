Build an account service with a JSON API under `/api` and a web frontend. People register an account, sign in, view and edit their profile and change their password. The admin lists accounts and can deactivate them.

API:

- `POST /api/auth/register` creates an account from a `username`, a `password` and an optional `email` and `display_name`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/auth/register`: `username` is required, unique and at most 50 characters; `password` is required and at least 8 characters long. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login`: It answers 401 for a wrong username or password, without saying which of the two was wrong.
- `GET /api/me` returns the signed-in account: `id`, `username`, `email`, `display_name`, `is_admin`, `created_at`.
- `PATCH /api/me` changes the signed-in account's `email` or `display_name`. It answers 400 for invalid input.
- `POST /api/me/password` changes the password; the body holds `current_password` and `new_password`. The new password must be at least 8 characters long; a wrong current password answers 403. On success it answers 204. It answers 400 for invalid input; 403 for a wrong current password.
- `GET /api/admin/users` lists every account, newest first, for the admin. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/admin/users/{id}/toggle` deactivates or reactivates an account, for the admin. The admin cannot deactivate its own account (400). It answers 400 for the admin's own account; 404 for an unknown id.

Data:

- `GET /api/admin/users` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an object with `id`, `username` and `is_active`.

Rules:

- A deactivated account cannot sign in, and requests made with its existing credentials answer 401.
- Passwords are never returned by any endpoint.
- Usernames are unique ignoring case.

Edge cases:

- Signing in with a wrong password and with an unknown username give the same response.
- Changing the password ends the account's other signed-in sessions.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- A card with 'Sign in' and 'Register' tabs; registration checks required fields and the 8-character minimum before sending and shows the API's messages.
- After sign-in: a profile panel with username, email, display name and member-since date, an edit form and a change-password form.
- When a request answers 401, the app returns to the sign-in card with a 'Please sign in again' message.
- For the admin: the account list with a deactivate/reactivate button per row, disabled on the admin's own row.

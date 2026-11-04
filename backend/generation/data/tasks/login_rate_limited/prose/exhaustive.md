Build an account service with a JSON API under `/api` and a web frontend that protects sign-in against guessing. A username is locked for 15 minutes after 5 consecutive failed sign-ins, and each client may make at most 20 sign-in attempts per minute. The admin can unlock a username early.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required and at least 8 characters long. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/me` returns the signed-in account's `id`, `username` and `last_login_at`.
- `GET /api/me/login-attempts` lists the account's 20 most recent sign-in attempts, newest first, each with `at` and `success`.
- `GET /api/admin/locks` lists the currently locked usernames with `locked_until`, for the admin.
- `POST /api/admin/locks/{username}/unlock` unlocks a username and resets its failure count, for the admin. On success it answers 204. It answers 404 for a username that is not locked.

Data:

- `GET /api/me/login-attempts` returns `{"items": [...]}`; each item in `items` is an object with `at` and `success`.
- `GET /api/admin/locks` returns `{"items": [...]}`; each item in `items` is an object with `username` and `locked_until`.

Rules:

- After 5 consecutive failed sign-ins for a username, every sign-in for it, even with the right password, answers 429 for 15 minutes; a successful sign-in resets the count.
- A client that makes more than 20 sign-in attempts within one minute gets 429 for the rest of that minute.
- A 429 response carries a `Retry-After` header with the number of seconds to wait.

Edge cases:

- Failed sign-ins for unknown usernames count towards the per-client limit and are answered exactly like wrong passwords.
- The lockout of one username does not affect other usernames.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- When sign-in is refused because of the lockout or the attempt limit, the form says so and shows when to try again.
- An account page with the last sign-in time and the recent sign-in attempts.
- For the admin: the locked usernames with an 'Unlock' button.

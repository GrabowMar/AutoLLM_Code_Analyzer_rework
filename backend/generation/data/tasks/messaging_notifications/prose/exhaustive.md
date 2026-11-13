Build a notification center with a JSON API under `/api` and a web frontend. Each user has a private inbox of notifications they can filter, mark as read or unread, and delete. The admin can broadcast an announcement into every user's inbox.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/notifications` lists the user's notifications, newest first, as a paged list with `unread_count`, the user's unread notifications regardless of filters. `read=true` or `read=false` keeps read or unread notifications; `type` keeps one type; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/notifications` adds an unread notification to the signed-in user's own inbox. On success it answers 201. It answers 400 for invalid input.
- `POST /api/notifications/read-all` marks every unread notification of the user as read and returns `updated`, how many changed.
- `GET /api/notifications/{id}` returns one notification. It answers 404 for an unknown notification or one of another user.
- `PATCH /api/notifications/{id}` sets `read` to true or false. It answers 400 for invalid input; 404 for an unknown notification or one of another user.
- `DELETE /api/notifications/{id}` deletes a notification. On success it answers 204. It answers 404 for an unknown notification or one of another user.
- `POST /api/admin/broadcast` adds the same notification to every user's inbox and returns `delivered`, the number of inboxes, for the admin. On success it answers 201. It answers 400 for invalid input.

Data:

- A notification is returned as an object with `id`, `message`, `type`, `read`, `created_at`.
- `GET /api/notifications` returns `{"items": [...], "total": n, "page": n, "page_size": n, "unread_count": n}`; each item in `items` is a notification.

Validation (invalid input answers 400 and changes nothing):

- Notification: `message` required, at most 500 characters; `type` optional, one of `info`, `warning`, `error`, default `info`; `read` optional on update, `true` or `false`; new notifications are unread.

Rules:

- Each user sees and changes only their own notifications; another user's notification behaves as if it did not exist.
- Messages are plain text.

Edge cases:

- 'Mark all as read' on an inbox without unread notifications answers 200 with `updated: 0`.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A bell icon with the unread count in the header.
- The inbox with type badges, unread ones in bold, filters for read state and type, 'Mark all as read', per-item read/unread toggles, delete and paging.
- A small form to add a notification to the own inbox.
- For the admin: a broadcast form.

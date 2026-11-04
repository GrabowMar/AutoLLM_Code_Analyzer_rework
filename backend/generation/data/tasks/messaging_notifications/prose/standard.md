Build a notification center with a JSON API under `/api` and a web frontend. Each user has a private inbox of notifications they can filter, mark as read or unread, and delete. The admin can broadcast an announcement into every user's inbox.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/notifications` lists the user's notifications, newest first, as a paged list with `unread_count`, the user's unread notifications regardless of filters (query parameters: `read`, `type`, `page`, `page_size`).
- `POST /api/notifications` adds an unread notification to the signed-in user's own inbox.
- `POST /api/notifications/read-all` marks every unread notification of the user as read and returns `updated`, how many changed.
- `GET /api/notifications/{id}` returns one notification.
- `PATCH /api/notifications/{id}` sets `read` to true or false.
- `DELETE /api/notifications/{id}` deletes a notification.
- `POST /api/admin/broadcast` adds the same notification to every user's inbox and returns `delivered`, the number of inboxes, for the admin.

Data:

- A notification is returned as an object with `id`, `message`, `type`, `read`, `created_at`.
- `GET /api/notifications` returns `{"items": [...], "total": n, "page": n, "page_size": n, "unread_count": n}`; each item in `items` is a notification.

Rules:

- Each user sees and changes only their own notifications; another user's notification behaves as if it did not exist.
- Messages are plain text.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A bell icon with the unread count in the header.
- The inbox with type badges, unread ones in bold, filters for read state and type, 'Mark all as read', per-item read/unread toggles, delete and paging.
- A small form to add a notification to the own inbox.
- For the admin: a broadcast form.

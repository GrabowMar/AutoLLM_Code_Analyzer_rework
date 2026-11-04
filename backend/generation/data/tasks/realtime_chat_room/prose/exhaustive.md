Build a single shared chat room with a JSON API under `/api`, a WebSocket for live delivery, and a web frontend. Anyone can read the history and listen live; signed-in users send messages and typing notices. The admin hides abusive messages.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/messages` stores a message from the signed-in user and broadcasts it to every connected WebSocket client. On success it answers 201. It answers 400 for invalid input.
- `GET /api/messages` returns the most recent visible messages in ascending id order. `limit` is how many of the most recent messages (default 50, at most 100); `before` returns only messages with an id below the given id, to load older history.
- `GET /api/ws/chat` is the live room: every client receives `{"type": "message", "id", "username", "text", "created_at"}` frames for new messages; signed-in clients may send `{"type": "message", "text"}` and `{"type": "typing"}`, which the others receive as `{"type": "typing", "username"}`. A client signs in for the socket the same way it does for the API. A frame the server cannot accept (not signed in, empty or too long text, unknown type) is answered with `{"type": "error", "detail": "<message>"}` to that client only, and the connection stays open. Typing notices are not stored.
- `POST /api/admin/messages/{id}/toggle` hides a message from the history or shows it again, for the admin. It answers 404 for an unknown id.

Data:

- A message is returned as an object with `id`, `text`, `created_at`, `is_hidden`, `username`.
- `GET /api/messages` returns `{"items": [...], "total": n}`; each item in `items` is a message.

Validation (invalid input answers 400 and changes nothing):

- Message: `text` required, at most 1000 characters after trimming, not empty.

Rules:

- Messages are stored durably and never edited; hidden messages are left out of the history.
- A message sent over the socket is stored and broadcast exactly like one sent with `POST /api/messages`.
- Message text is plain text and is shown as such.

Edge cases:

- Leading and trailing whitespace is trimmed before the length check.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- The room with the message list (username, text, time), automatic scrolling to new messages and a 'Load older' button.
- For signed-in users: a message box that sends on Enter and a 'someone is typing' line for others.
- A connection indicator that reconnects automatically when the socket drops.
- For the admin: a hide button on each message.

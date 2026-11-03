Build a notes app with a JSON API under `/api` and a web frontend. Notes are public to read; signed-in users write notes with tags and pin the important ones; only a note's author may change or delete it.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/notes` lists notes, pinned first and then most recently updated, as a paged list. `tag` keeps notes carrying that tag (case-insensitive); `author` keeps notes by that username; `q` matches title or content case-insensitively as a substring; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/notes` adds a note by the signed-in user. On success it answers 201. It answers 400 for invalid input.
- `GET /api/notes/{id}` returns one note. It answers 404 for an unknown id.
- `PATCH /api/notes/{id}` changes only the fields it is given; only the author may do this. It answers 400 for invalid input; 403 for a note of another user; 404 for an unknown id.
- `DELETE /api/notes/{id}` deletes a note; only the author may do this. On success it answers 204. It answers 403 for a note of another user; 404 for an unknown id.
- `GET /api/tags` lists every tag in use with the number of notes carrying it, most used first.

Data:

- A note is returned as an object with `id`, `title`, `content`, `tags`, `pinned`, `created_at`, `updated_at`, `author`.
- `GET /api/notes` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a note.
- `GET /api/tags` returns `{"items": [...]}`; each item in `items` is an object with `tag` and `count`.

Validation (invalid input answers 400 and changes nothing):

- Note: `title` required, at most 200 characters; `content` optional, at most 20000 characters; `tags` optional, at most 10 tags; each is trimmed and lower-cased, 1 to 30 characters, duplicates dropped; `pinned` optional, `true` or `false`, default `false`.

Rules:

- Anyone may read notes; only the author may change or delete one.
- `updated_at` changes on every update.
- Note content is plain text and is shown as such.

Edge cases:

- Tags `Work`, ` work ` and `work` are the same tag.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A note list (pinned ones marked) with a search box, an author filter, paging and a tag cloud whose tags filter the list.
- A note page that shows the content as plain text with line breaks kept.
- Create and edit forms with a tag input, showing the API's validation messages; delete with confirmation on the user's own notes.

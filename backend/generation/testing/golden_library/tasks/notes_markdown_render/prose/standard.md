Build a notes app with a JSON API under `/api`.

- `POST /api/auth/register` and `POST /api/auth/login` manage accounts; login returns a bearer token.
- `GET /api/notes` lists the user's notes and `POST /api/notes` adds one (title and Markdown body).
- `GET /api/notes/{id}/html` returns the note rendered as HTML.

Each user reads only their own notes.

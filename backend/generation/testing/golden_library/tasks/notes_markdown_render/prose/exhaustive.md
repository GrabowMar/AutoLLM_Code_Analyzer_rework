Build a notes app with a JSON API under `/api`.

- `POST /api/auth/register` and `POST /api/auth/login` manage accounts; login returns a bearer token.
- `GET /api/notes` lists the user's notes and `POST /api/notes` adds one. The title is required and at
  most 120 characters; the body is required Markdown (400 otherwise).
- `GET /api/notes/{id}/html` returns the note rendered as HTML (404 for an unknown note).

Each user reads only their own notes.

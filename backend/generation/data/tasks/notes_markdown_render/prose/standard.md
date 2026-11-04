Build a Markdown notes app with a JSON API under `/api` and a web frontend. Users write private notes in Markdown and read them rendered as HTML; a note can be published, which makes its rendered page readable by anyone.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/notes` lists the user's notes, most recently updated first, without their bodies.
- `POST /api/notes` adds a note.
- `GET /api/notes/{id}` returns one note with its Markdown body.
- `PATCH /api/notes/{id}` changes `title`, `body` or `published`.
- `DELETE /api/notes/{id}` deletes a note.
- `GET /api/notes/{id}/html` returns the note's body rendered from Markdown as an HTML fragment (`text/html`).
- `GET /api/public/notes/{id}/html` returns the rendered HTML of a published note to anyone.

Data:

- A note is returned as an object with `id`, `title`, `body`, `published`, `updated_at`, `author`.
- `GET /api/notes` returns `{"items": [...]}`; each item in `items` is an object with `id`, `title`, `published` and `updated_at`.

Rules:

- Each user sees and changes only their own notes; another user's note behaves as if it did not exist.
- An unpublished note is never visible to anyone but its author.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- The user's notes with a published badge.
- A Markdown editor with a live rendered preview (the preview uses the API's rendering) and a 'Published' switch with a copyable public link.
- A public page that shows a published note's rendered HTML under its title and author.

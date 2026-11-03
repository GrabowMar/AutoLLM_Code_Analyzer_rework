Build a Markdown notes app with a JSON API under `/api` and a web frontend. Users write private notes in Markdown and read them rendered as HTML; a note can be published, which makes its rendered page readable by anyone.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/notes` lists the user's notes, most recently updated first, without their bodies.
- `POST /api/notes` adds a note. On success it answers 201. It answers 400 for invalid input.
- `GET /api/notes/{id}` returns one note with its Markdown body. It answers 404 for an unknown note or one of another user.
- `PATCH /api/notes/{id}` changes `title`, `body` or `published`. It answers 400 for invalid input; 404 for an unknown note or one of another user.
- `DELETE /api/notes/{id}` deletes a note. On success it answers 204. It answers 404 for an unknown note or one of another user.
- `GET /api/notes/{id}/html` returns the note's body rendered from Markdown as an HTML fragment (`text/html`). The rendering supports headings, emphasis, lists, links, images, block quotes, inline code and fenced code blocks. It answers 404 for an unknown note or one of another user.
- `GET /api/public/notes/{id}/html` returns the rendered HTML of a published note to anyone. It answers 404 for an unknown or unpublished note.

Data:

- A note is returned as an object with `id`, `title`, `body`, `published`, `updated_at`, `author`.
- `GET /api/notes` returns `{"items": [...]}`; each item in `items` is an object with `id`, `title`, `published` and `updated_at`.

Validation (invalid input answers 400 and changes nothing):

- Note: `title` required, at most 120 characters; `body` required, Markdown, at most 50000 characters; `published` optional, `true` or `false`, default `false`.

Rules:

- Each user sees and changes only their own notes; another user's note behaves as if it did not exist.
- An unpublished note is never visible to anyone but its author.

Edge cases:

- Unpublishing a note makes its public page answer 404 at once.
- An empty-looking body (only whitespace) answers 400.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- The user's notes with a published badge.
- A Markdown editor with a live rendered preview (the preview uses the API's rendering) and a 'Published' switch with a copyable public link.
- A public page that shows a published note's rendered HTML under its title and author.

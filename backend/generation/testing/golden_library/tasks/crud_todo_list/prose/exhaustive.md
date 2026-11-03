Build a todo list app with a JSON API under `/api` and a web frontend.

- `POST /api/auth/register` creates an account from a username (at most 50 characters, unique) and a
  password. It answers 201, 400 for invalid input and 409 when the username is taken.
- `POST /api/auth/login` returns `{"token": ...}`, or 401 for wrong credentials. The other endpoints
  require the token as a bearer token.
- `GET /api/todos` lists the user's todos as `{"items": [...]}`; `q` searches the titles
  (case-insensitive substring).
- `POST /api/todos` adds a todo; the title is required and at most 200 characters (400 otherwise). It
  answers 201 with the todo.
- `PATCH /api/todos/{id}` changes the title or `done`. It answers 404 for an unknown todo.
- `DELETE /api/todos/{id}` removes a todo and answers 204.

Each user sees and changes only their own todos: a todo of another user answers 403 or 404.

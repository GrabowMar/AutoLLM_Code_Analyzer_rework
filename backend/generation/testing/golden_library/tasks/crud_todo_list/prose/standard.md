Build a todo list app with a JSON API under `/api` and a web frontend.

- `POST /api/auth/register` creates an account from a username and a password.
- `POST /api/auth/login` returns a token; the other endpoints require it as a bearer token.
- `GET /api/todos` lists the user's todos as `{"items": [...]}`; `q` searches the titles.
- `POST /api/todos` adds a todo with a title.
- `PATCH /api/todos/{id}` changes the title or marks the todo done.
- `DELETE /api/todos/{id}` removes a todo.

Each user sees and changes only their own todos.

Build a todo list app with a JSON API under `/api` and a web frontend. Each user keeps a private list of todos with a title, an optional description, a priority, an optional due date and a completed flag.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/todos` lists the signed-in user's todos as a paged list: open todos first, then by due date (todos without one last), then newest first. `remaining` counts the user's open todos regardless of the filters. `completed=true` or `completed=false` keeps completed or open todos; `priority` keeps one priority; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 50, at most 200).
- `POST /api/todos` adds a todo for the signed-in user and returns it. On success it answers 201. It answers 400 for invalid input.
- `GET /api/todos/{id}` returns one todo. It answers 404 for an unknown todo or one of another user.
- `PATCH /api/todos/{id}` changes only the fields it is given and returns the todo. Sending `due_date: null` clears the due date. It answers 400 for invalid input; 404 for an unknown todo or one of another user.
- `DELETE /api/todos/{id}` deletes a todo permanently. On success it answers 204. It answers 404 for an unknown todo or one of another user.

Data:

- A todo is returned as an object with `id`, `title`, `description`, `priority`, `due_date`, `completed`, `completed_at`, `created_at`.
- `GET /api/todos` returns `{"items": [...], "total": n, "page": n, "page_size": n, "remaining": n}`; each item in `items` is a todo.

Validation (invalid input answers 400 and changes nothing):

- Todo: `title` required, at most 255 characters; `description` optional, at most 2000 characters; `priority` optional, one of `low`, `medium`, `high`, default `medium`; `due_date` optional, a date `YYYY-MM-DD`; `completed` optional, `true` or `false`, default `false`.

Rules:

- Each user sees and changes only their own todos; another user's todo behaves as if it did not exist.
- `completed_at` is set when a todo becomes completed and cleared when it is reopened.

Edge cases:

- A page past the end returns an empty `items` list with the correct `total`.
- Changing `completed` to its current value keeps `completed_at` unchanged.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- The todo list with title, priority, due date (overdue open todos highlighted) and a checkbox that saves immediately; a counter of open todos.
- Tabs for all, open and completed todos, a priority filter and a 'Load more' button.
- An add form and inline editing that show the API's validation messages; delete asks for confirmation.

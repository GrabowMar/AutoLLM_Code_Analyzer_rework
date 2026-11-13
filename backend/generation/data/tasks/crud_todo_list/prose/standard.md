Build a todo list app with a JSON API under `/api` and a web frontend. Each user keeps a private list of todos with a title, an optional description, a priority, an optional due date and a completed flag.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/todos` lists the signed-in user's todos as a paged list: open todos first, then by due date (todos without one last), then newest first (query parameters: `completed`, `priority`, `page`, `page_size`).
- `POST /api/todos` adds a todo for the signed-in user and returns it.
- `GET /api/todos/{id}` returns one todo.
- `PATCH /api/todos/{id}` changes only the fields it is given and returns the todo.
- `DELETE /api/todos/{id}` deletes a todo permanently.

Data:

- A todo is returned as an object with `id`, `title`, `description`, `priority`, `due_date`, `completed`, `completed_at`, `created_at`.
- `GET /api/todos` returns `{"items": [...], "total": n, "page": n, "page_size": n, "remaining": n}`; each item in `items` is a todo.

Rules:

- Each user sees and changes only their own todos; another user's todo behaves as if it did not exist.
- `completed_at` is set when a todo becomes completed and cleared when it is reopened.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- The todo list with title, priority, due date (overdue open todos highlighted) and a checkbox that saves immediately; a counter of open todos.
- Tabs for all, open and completed todos, a priority filter and a 'Load more' button.
- An add form and inline editing that show the API's validation messages; delete asks for confirmation.

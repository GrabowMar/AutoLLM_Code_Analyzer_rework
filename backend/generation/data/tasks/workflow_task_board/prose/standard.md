Build a shared kanban board with a JSON API under `/api` and a web frontend. Anyone can view the board; signed-in users create, edit, move, reorder and delete tasks across the columns To Do, In Progress and Done.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/tasks` lists tasks ordered by column (`todo`, `in_progress`, `done`) and then position, with `counts` per status (query parameters: `status`, `priority`).
- `POST /api/tasks` creates a task at the end of its column.
- `GET /api/tasks/{id}` returns one task.
- `PATCH /api/tasks/{id}` changes only the fields it is given, including moving the task to another column or position.
- `DELETE /api/tasks/{id}` deletes a task and closes the gap in its column.

Data:

- A task is returned as an object with `id`, `title`, `description`, `status`, `priority`, `position`, `updated_at`.
- `GET /api/tasks` returns `{"items": [...], "total": n, "counts": {...}}`; each item in `items` is a task.

Rules:

- Any signed-in user may change any task.
- `position` is a task's 1-based place within its column; each column's tasks always hold positions 1 to n without gaps.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Three columns with task cards (title, priority badge) and per-column counts, plus a priority filter.
- For signed-in users: drag and drop between and within columns that saves the new column and position.
- Create and edit dialogs showing the API's validation messages; delete with confirmation.

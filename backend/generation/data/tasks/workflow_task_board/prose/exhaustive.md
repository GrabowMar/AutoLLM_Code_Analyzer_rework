Build a shared kanban board with a JSON API under `/api` and a web frontend. Anyone can view the board; signed-in users create, edit, move, reorder and delete tasks across the columns To Do, In Progress and Done.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/tasks` lists tasks ordered by column (`todo`, `in_progress`, `done`) and then position, with `counts` per status. `status` keeps one column; `priority` keeps one priority.
- `POST /api/tasks` creates a task at the end of its column. On success it answers 201. It answers 400 for invalid input.
- `GET /api/tasks/{id}` returns one task. It answers 404 for an unknown id.
- `PATCH /api/tasks/{id}` changes only the fields it is given, including moving the task to another column or position. A new `status` without a `position` moves the task to the end of the new column; a `position` places it there and shifts the others. It answers 400 for invalid input; 404 for an unknown id.
- `DELETE /api/tasks/{id}` deletes a task and closes the gap in its column. On success it answers 204. It answers 404 for an unknown id.

Data:

- A task is returned as an object with `id`, `title`, `description`, `status`, `priority`, `position`, `updated_at`.
- `GET /api/tasks` returns `{"items": [...], "total": n, "counts": {...}}`; each item in `items` is a task.

Validation (invalid input answers 400 and changes nothing):

- Task: `title` required, at most 200 characters; `description` optional, at most 2000 characters; `status` optional, one of `todo`, `in_progress`, `done`, default `todo`; `priority` optional, one of `low`, `medium`, `high`, default `medium`; `position` optional on update, a whole number of at least 1; a position past the end places the task last.

Rules:

- Any signed-in user may change any task.
- `position` is a task's 1-based place within its column; each column's tasks always hold positions 1 to n without gaps.

Edge cases:

- Moving a task within its own column to its current position changes nothing.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Three columns with task cards (title, priority badge) and per-column counts, plus a priority filter.
- For signed-in users: drag and drop between and within columns that saves the new column and position.
- Create and edit dialogs showing the API's validation messages; delete with confirmation.

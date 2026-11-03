Build a personal timesheet with a JSON API under `/api` and a web frontend. Users log time entries (project, description, hours, date), see totals per project and export their entries as CSV files that open in spreadsheet programs.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/entries` lists the user's entries, newest date first, as a paged list (query parameters: `start_date`, `end_date`, `project`, `page`, `page_size`).
- `POST /api/entries` logs an entry.
- `PATCH /api/entries/{id}` changes only the fields it is given.
- `DELETE /api/entries/{id}` deletes an entry.
- `GET /api/exports/entries.csv` downloads the user's entries matching the filters as a CSV file (`text/csv`, UTF-8) with the header row `date,project,description,hours`, oldest first (query parameters: `start_date`, `end_date`, `project`).
- `GET /api/exports/summary.csv` downloads the total hours per project for the filters as a CSV file with the header row `project,hours`, sorted by project (query parameters: `start_date`, `end_date`).

Data:

- A time entry is returned as an object with `id`, `project`, `description`, `hours`, `date`, `created_at`.
- `GET /api/entries` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a time entry.

Rules:

- Each user sees, changes and exports only their own entries; another user's entry behaves as if it did not exist.
- Exported cells must be read by spreadsheet programs as the text the user typed, never as something else.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A quick entry form (project with suggestions from earlier entries, description, hours, date defaulting to today) showing the API's validation messages.
- The entry list with period and project filters, inline editing, delete with confirmation, per-project totals and paging.
- 'Export CSV' and 'Export totals' buttons that download the files for the current filters.

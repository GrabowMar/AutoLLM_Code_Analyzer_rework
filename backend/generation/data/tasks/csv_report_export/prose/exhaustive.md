Build a personal timesheet with a JSON API under `/api` and a web frontend. Users log time entries (project, description, hours, date), see totals per project and export their entries as CSV files that open in spreadsheet programs.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/entries` lists the user's entries, newest date first, as a paged list. `start_date` and `end_date` keep entries dated within that inclusive range; `end_date` is inclusive; `project` keeps one project (exact, ignoring case); `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 50, at most 200).
- `POST /api/entries` logs an entry. On success it answers 201. It answers 400 for invalid input.
- `PATCH /api/entries/{id}` changes only the fields it is given. It answers 400 for invalid input; 404 for an unknown entry or one of another user.
- `DELETE /api/entries/{id}` deletes an entry. On success it answers 204. It answers 404 for an unknown entry or one of another user.
- `GET /api/exports/entries.csv` downloads the user's entries matching the filters as a CSV file (`text/csv`, UTF-8) with the header row `date,project,description,hours`, oldest first. The file is sent as an attachment named `timesheet-<start>-<end>.csv` (`all` for a missing bound). Fields follow RFC 4180 quoting, so commas, quotes and line breaks in a description survive a round trip through a spreadsheet program. `start_date` and `end_date` keep entries dated within that inclusive range; `end_date` is inclusive; `project` keeps one project (exact, ignoring case).
- `GET /api/exports/summary.csv` downloads the total hours per project for the filters as a CSV file with the header row `project,hours`, sorted by project. `start_date` and `end_date` keep entries dated within that inclusive range; `end_date` is inclusive.

Data:

- A time entry is returned as an object with `id`, `project`, `description`, `hours`, `date`, `created_at`.
- `GET /api/entries` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a time entry.

Validation (invalid input answers 400 and changes nothing):

- Time entry: `project` required, at most 100 characters; `description` required, at most 500 characters; `hours` required, a number from 0.25 to 24 in steps of 0.25; `date` required, a date `YYYY-MM-DD`.

Rules:

- Each user sees, changes and exports only their own entries; another user's entry behaves as if it did not exist.
- Exported cells must be read by spreadsheet programs as the text the user typed, never as something else.

Edge cases:

- Hours are written in the CSV with a dot as the decimal separator.
- An export without matching entries contains only the header row.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A quick entry form (project with suggestions from earlier entries, description, hours, date defaulting to today) showing the API's validation messages.
- The entry list with period and project filters, inline editing, delete with confirmation, per-project totals and paging.
- 'Export CSV' and 'Export totals' buttons that download the files for the current filters.

Build a personal expense tracker with a JSON API under `/api` and a web frontend. Each user records their own expenses and sees totals per category for a chosen period.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/expenses` lists the user's expenses, newest date first, as a paged list with `total_amount`, the sum over every matching expense (query parameters: `category`, `start_date`, `end_date`, `page`, `page_size`).
- `POST /api/expenses` records an expense.
- `GET /api/expenses/summary` returns `total_amount`, `count` and `by_category` (the amount per category) for the same filters (query parameters: `category`, `start_date`, `end_date`).
- `GET /api/expenses/{id}` returns one expense.
- `PUT /api/expenses/{id}` replaces an expense's fields; an omitted `description` is cleared.
- `DELETE /api/expenses/{id}` deletes an expense.

Data:

- An expense is returned as an object with `id`, `amount`, `category`, `description`, `date`, `created_at`.
- `GET /api/expenses` returns `{"items": [...], "total": n, "page": n, "page_size": n, "total_amount": n}`; each item in `items` is an expense.

Rules:

- Each user sees and changes only their own expenses; another user's expense behaves as if it did not exist.
- Totals and the summary cover only the signed-in user's expenses.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A total card and a per-category breakdown with amounts and shares for the current filters.
- The expense list with a category filter, a period filter ('This month', 'Last month', custom dates) and paging.
- Add and edit forms (date defaulting to today) showing the API's validation messages; delete asks for confirmation.

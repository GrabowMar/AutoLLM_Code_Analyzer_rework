Build a personal expense tracker with a JSON API under `/api` and a web frontend. Each user records their own expenses and sees totals per category for a chosen period.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/expenses` lists the user's expenses, newest date first, as a paged list with `total_amount`, the sum over every matching expense. `category` keeps one category; `start_date` and `end_date` keep items dated within that inclusive range (an `end_date` before `start_date` answers 400); `end_date` is inclusive; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 25, at most 100). It answers 400 for invalid input.
- `POST /api/expenses` records an expense. On success it answers 201. It answers 400 for invalid input.
- `GET /api/expenses/summary` returns `total_amount`, `count` and `by_category` (the amount per category) for the same filters. Every category appears in `by_category`, with 0 when it has no expenses; amounts are rounded to two decimals. `category` keeps one category; `start_date` and `end_date` keep items dated within that inclusive range (an `end_date` before `start_date` answers 400); `end_date` is inclusive. It answers 400 for invalid input.
- `GET /api/expenses/{id}` returns one expense. It answers 404 for an unknown expense or one of another user.
- `PUT /api/expenses/{id}` replaces an expense's fields; an omitted `description` is cleared. It answers 400 for invalid input; 404 for an unknown expense or one of another user.
- `DELETE /api/expenses/{id}` deletes an expense. On success it answers 204. It answers 404 for an unknown expense or one of another user.

Data:

- An expense is returned as an object with `id`, `amount`, `category`, `description`, `date`, `created_at`.
- `GET /api/expenses` returns `{"items": [...], "total": n, "page": n, "page_size": n, "total_amount": n}`; each item in `items` is an expense.

Validation (invalid input answers 400 and changes nothing):

- Expense: `amount` required, a number above 0 and at most 1000000 with two decimals; `category` required, one of `food`, `transport`, `housing`, `utilities`, `entertainment`, `health`, `other`; `description` optional, at most 500 characters; `date` required, a date `YYYY-MM-DD` not in the future.

Rules:

- Each user sees and changes only their own expenses; another user's expense behaves as if it did not exist.
- Totals and the summary cover only the signed-in user's expenses.

Edge cases:

- An `end_date` before `start_date` answers 400.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A total card and a per-category breakdown with amounts and shares for the current filters.
- The expense list with a category filter, a period filter ('This month', 'Last month', custom dates) and paging.
- Add and edit forms (date defaulting to today) showing the API's validation messages; delete asks for confirmation.

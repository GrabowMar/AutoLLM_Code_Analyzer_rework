Build a sales dashboard with a JSON API under `/api` and a web frontend. Anyone can explore the sales as a table, headline totals and a daily chart, filtered by date range and product; signed-in users record, edit and delete sales.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/sales` lists sales, newest date first, as a paged list. `start_date` and `end_date` keep items dated within that inclusive range (an `end_date` before `start_date` answers 400); `end_date` is inclusive; `product` keeps sales of exactly that product, ignoring case; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 25, at most 100).
- `POST /api/sales` records a sale. On success it answers 201. It answers 400 for invalid input.
- `GET /api/sales/summary` returns `total_sales` (sum of amounts), `total_transactions`, `average_sale` and `total_quantity` for the same filters. `average_sale` is rounded to two decimals and is 0 without sales. `start_date` and `end_date` keep items dated within that inclusive range (an `end_date` before `start_date` answers 400); `end_date` is inclusive; `product` keeps sales of exactly that product, ignoring case. It answers 400 for invalid input.
- `GET /api/sales/daily` returns one entry per day that has sales, oldest first, with that day's `date`, `total` and `count`, for the same filters. `start_date` and `end_date` keep items dated within that inclusive range (an `end_date` before `start_date` answers 400); `end_date` is inclusive; `product` keeps sales of exactly that product, ignoring case. It answers 400 for invalid input.
- `GET /api/sales/{id}` returns one sale. It answers 404 for an unknown id.
- `PUT /api/sales/{id}` replaces a sale's fields. It answers 400 for invalid input; 404 for an unknown id.
- `DELETE /api/sales/{id}` deletes a sale. On success it answers 204. It answers 404 for an unknown id.

Data:

- A sale is returned as an object with `id`, `product`, `amount`, `quantity`, `date`, `created_at`.
- `GET /api/sales` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a sale.
- `GET /api/sales/daily` returns `{"items": [...]}`; each item in `items` is an object with `date`, `total` and `count`.

Validation (invalid input answers 400 and changes nothing):

- Sale: `product` required, at most 100 characters; `amount` required, the sale's total value, a number above 0 and at most 1000000 with two decimals; `quantity` required, a whole number of at least 1; `date` optional, a date `YYYY-MM-DD` not in the future; default today.

Rules:

- Any signed-in user may change any sale.
- On first start with no sales the app holds about 40 sample sales spread over the previous 60 days.
- Amounts are returned as numbers with two decimals.

Edge cases:

- The summary and the daily series apply exactly the same filters as the list.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Headline cards for total sales, number of transactions and average sale.
- A bar chart of daily totals.
- A date range filter ('This month', 'Last month', custom dates) and a product filter that the cards, chart and table all follow.
- A sales table with date, product, quantity and amount and paging; add and edit forms showing the API's validation messages; delete asks for confirmation.

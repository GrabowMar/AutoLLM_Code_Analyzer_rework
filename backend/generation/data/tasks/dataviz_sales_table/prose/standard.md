Build a sales dashboard with a JSON API under `/api` and a web frontend. Anyone can explore the sales as a table, headline totals and a daily chart, filtered by date range and product; signed-in users record, edit and delete sales.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/sales` lists sales, newest date first, as a paged list (query parameters: `start_date`, `end_date`, `product`, `page`, `page_size`).
- `POST /api/sales` records a sale.
- `GET /api/sales/summary` returns `total_sales` (sum of amounts), `total_transactions`, `average_sale` and `total_quantity` for the same filters (query parameters: `start_date`, `end_date`, `product`).
- `GET /api/sales/daily` returns one entry per day that has sales, oldest first, with that day's `date`, `total` and `count`, for the same filters (query parameters: `start_date`, `end_date`, `product`).
- `GET /api/sales/{id}` returns one sale.
- `PUT /api/sales/{id}` replaces a sale's fields.
- `DELETE /api/sales/{id}` deletes a sale.

Data:

- A sale is returned as an object with `id`, `product`, `amount`, `quantity`, `date`, `created_at`.
- `GET /api/sales` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a sale.
- `GET /api/sales/daily` returns `{"items": [...]}`; each item in `items` is an object with `date`, `total` and `count`.

Rules:

- Any signed-in user may change any sale.
- On first start with no sales the app holds about 40 sample sales spread over the previous 60 days.
- Amounts are returned as numbers with two decimals.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Headline cards for total sales, number of transactions and average sale.
- A bar chart of daily totals.
- A date range filter ('This month', 'Last month', custom dates) and a product filter that the cards, chart and table all follow.
- A sales table with date, product, quantity and amount and paging; add and edit forms showing the API's validation messages; delete asks for confirmation.

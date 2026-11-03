Build a subscription billing console with a JSON API under `/api` and a web frontend for a small team. Every signed-in team member may read and change every subscription. The console issues invoices, records the outcome of payment collection runs and shows monthly recurring revenue (MRR). Visitors see only a public summary.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/subscriptions` creates a subscription.
- `GET /api/subscriptions` lists subscriptions, newest first, as a paged list (query parameters: `q`, `status`, `plan`, `page`, `page_size`).
- `GET /api/subscriptions/{id}` returns one subscription.
- `PATCH /api/subscriptions/{id}` changes only the fields it is given.
- `GET /api/subscriptions/{id}/invoices` lists a subscription's invoices, newest first.
- `POST /api/subscriptions/{id}/invoices` issues an open invoice.
- `POST /api/subscriptions/{id}/collections` records the `outcome` (`failed` or `succeeded`) of one collection run over the subscription's open invoices.
- `GET /api/public/stats` returns `mrr` (the sum of `monthly_amount` over active and past-due subscriptions) and `counts` per status.

Data:

- A subscription is returned as an object with `id`, `customer_name`, `plan`, `monthly_amount`, `renewal_date`, `status`, `failed_payment_count`, `created_at`.
- An invoice is returned as an object with `id`, `subscription_id`, `amount_due`, `due_date`, `status`, `paid_at`.
- `GET /api/subscriptions` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a subscription.
- `GET /api/subscriptions/{id}/invoices` returns `{"items": [...]}`; each item in `items` is an invoice.

Rules:

- Every signed-in team member may read and change every subscription; visitors only see the public summary.
- Trial and cancelled subscriptions do not count towards MRR.
- Money amounts are returned as numbers with at most two decimals.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A public page with MRR and the number of subscriptions per status.
- A subscription table with status tabs, a plan filter, a customer search and paging.
- A subscription page with an edit form, the invoice history, an 'Issue invoice' form and buttons to record a failed or successful collection run.

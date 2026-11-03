Build a subscription billing console with a JSON API under `/api` and a web frontend for a small team. Every signed-in team member may read and change every subscription. The console issues invoices, records the outcome of payment collection runs and shows monthly recurring revenue (MRR). Visitors see only a public summary.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/subscriptions` creates a subscription. On success it answers 201. It answers 400 for invalid input.
- `GET /api/subscriptions` lists subscriptions, newest first, as a paged list. `q` matches `customer_name` case-insensitively as a substring; `status` keeps one status; `plan` keeps one plan; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/subscriptions/{id}` returns one subscription. It answers 404 for an unknown id.
- `PATCH /api/subscriptions/{id}` changes only the fields it is given. A cancelled subscription accepts no change except its status back to `trial` or `active`. It answers 400 for invalid input; 404 for an unknown id; 409 for a change a cancelled subscription does not allow.
- `GET /api/subscriptions/{id}/invoices` lists a subscription's invoices, newest first. It answers 404 for an unknown id.
- `POST /api/subscriptions/{id}/invoices` issues an open invoice. On success it answers 201. It answers 400 for invalid input; 404 for an unknown id; 409 for a cancelled subscription.
- `POST /api/subscriptions/{id}/collections` records the `outcome` (`failed` or `succeeded`) of one collection run over the subscription's open invoices. `failed` adds 1 to `failed_payment_count` and sets an `active` or `trial` subscription to `past_due`; `succeeded` marks every open invoice paid, resets `failed_payment_count` to 0 and sets a `past_due` subscription to `active`. Without open invoices nothing changes. The response is the updated subscription. It answers 400 for invalid input; 404 for an unknown id.
- `GET /api/public/stats` returns `mrr` (the sum of `monthly_amount` over active and past-due subscriptions) and `counts` per status.

Data:

- A subscription is returned as an object with `id`, `customer_name`, `plan`, `monthly_amount`, `renewal_date`, `status`, `failed_payment_count`, `created_at`.
- An invoice is returned as an object with `id`, `subscription_id`, `amount_due`, `due_date`, `status`, `paid_at`.
- `GET /api/subscriptions` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a subscription.
- `GET /api/subscriptions/{id}/invoices` returns `{"items": [...]}`; each item in `items` is an invoice.

Validation (invalid input answers 400 and changes nothing):

- Subscription: `customer_name` required, at most 200 characters; `plan` required, one of `starter`, `growth`, `enterprise`; `monthly_amount` required, a number above 0 and at most 100000 with at most two decimals; `renewal_date` required, a date `YYYY-MM-DD`; `status` optional, one of `trial`, `active`, `past_due`, `cancelled`, default `trial`.
- Invoice: `amount_due` optional, a number above 0; default the subscription's `monthly_amount`; `due_date` optional, a date `YYYY-MM-DD` not before today; default 14 days after issue.

Rules:

- Every signed-in team member may read and change every subscription; visitors only see the public summary.
- Trial and cancelled subscriptions do not count towards MRR.
- Money amounts are returned as numbers with at most two decimals.

Edge cases:

- A succeeded collection run without open invoices answers 200 and changes nothing.
- Subscriptions and invoices are never deleted.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A public page with MRR and the number of subscriptions per status.
- A subscription table with status tabs, a plan filter, a customer search and paging.
- A subscription page with an edit form, the invoice history, an 'Issue invoice' form and buttons to record a failed or successful collection run.

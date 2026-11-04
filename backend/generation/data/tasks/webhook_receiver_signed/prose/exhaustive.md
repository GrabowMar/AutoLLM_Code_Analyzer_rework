Build an API-only service under `/api` that receives payment webhooks from a payment provider, verifies them and keeps the payment state of orders. Requests are signed with a secret shared with the provider, which the app reads from the environment variable `WEBHOOK_SECRET` at start-up. The admin reads orders and the event log.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/webhooks/payments` receives one event `{"id", "type", "created", "data": {"order_id", "amount", "currency"}}` where `type` is `payment.succeeded`, `payment.failed` or `payment.refunded`, and updates the order (creating it when new). A missing or wrong signature, or a timestamp more than 300 seconds away from the server's clock, answers 401 and changes nothing. An event id that was already applied answers 200 with `{"result": "duplicate"}` and changes nothing; an unknown `type` answers 200 with `{"result": "ignored"}`; otherwise 200 with `{"result": "applied"}`. It answers 400 for a body that is not a valid event; 401 for a missing or invalid signature or a stale timestamp.
- `GET /api/admin/orders` lists orders with their payment status, most recently updated first, for the admin. `status` keeps one status; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/admin/orders/{order_id}` returns one order with the events applied to it, for the admin. It answers 404 for an unknown id.
- `GET /api/admin/events` lists received events with their `result`, newest first, for the admin. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).

Data:

- An order is returned as an object with `id`, `order_id`, `status`, `amount`, `currency`, `updated_at`.
- A webhook event is returned as an object with `id`, `event_id`, `type`, `received_at`, `result`.
- `POST /api/webhooks/payments` takes a JSON object with `id`, `type`, `created` and `data`.
- `GET /api/admin/orders` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an order.
- `GET /api/admin/orders/{order_id}` returns the order with `events` as well.
- `GET /api/admin/events` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a webhook event.

Rules:

- Every webhook request carries the headers `X-Timestamp` (Unix seconds) and `X-Signature`, which is `sha256=` followed by the hex HMAC-SHA256 of `<X-Timestamp>.<raw request body>` with `WEBHOOK_SECRET` as the key; a request with a missing or wrong signature answers 401 and changes nothing.
- `payment.succeeded` sets an order to `paid`, `payment.failed` to `failed` and `payment.refunded` to `refunded`; a refund of an order that is not paid is recorded as `ignored`.
- The signature is checked against the exact bytes received, before the body is parsed.
- Rejected requests are not stored as events.

Edge cases:

- When `WEBHOOK_SECRET` is unset or empty the app accepts no webhooks (401) instead of falling back to a default.
- Events for the same order may arrive out of order; the order keeps the state of the latest `created` time.

Every error response is a JSON object `{"detail": "<message>"}`.

Build an API-only service under `/api` that receives payment webhooks from a payment provider, verifies them and keeps the payment state of orders. Requests are signed with a secret shared with the provider, which the app reads from the environment variable `WEBHOOK_SECRET` at start-up. The admin reads orders and the event log.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/webhooks/payments` receives one event `{"id", "type", "created", "data": {"order_id", "amount", "currency"}}` where `type` is `payment.succeeded`, `payment.failed` or `payment.refunded`, and updates the order (creating it when new).
- `GET /api/admin/orders` lists orders with their payment status, most recently updated first, for the admin (query parameters: `status`, `page`, `page_size`).
- `GET /api/admin/orders/{order_id}` returns one order with the events applied to it, for the admin.
- `GET /api/admin/events` lists received events with their `result`, newest first, for the admin (query parameters: `page`, `page_size`).

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

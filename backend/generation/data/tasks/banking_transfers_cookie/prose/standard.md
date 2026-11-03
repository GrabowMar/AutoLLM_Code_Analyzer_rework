Build a small online bank with a JSON API under `/api` and a web frontend served from the same origin. Every account starts with a balance of 1000.00; users send money to other users by username and see their transaction history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`; `POST /api/auth/logout` signs out. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/auth/logout` signs the current user out.
- `GET /api/auth/csrf` returns an anti-forgery token `{"csrf_token": "..."}` for the signed-in user.
- `GET /api/account` returns the signed-in user's `username` and `balance`.
- `POST /api/transfers` sends `amount` from the signed-in user to `to_username` with an optional `memo` and returns the transfer.
- `GET /api/transfers` lists the transfers the user sent or received, newest first, each with `direction` (`in` or `out`), as a paged list (query parameters: `page`, `page_size`).
- `GET /api/transfers/{id}` returns one transfer the user sent or received.

Data:

- A transfer is returned as an object with `id`, `to_username`, `amount`, `memo`, `created_at`, `from_username`, `direction`.
- `GET /api/transfers` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a transfer.

Rules:

- Every new account starts with a balance of 1000.00; money moves only through transfers.
- Users see only the transfers they sent or received.
- A transfer either happens completely (both balances change) or not at all.

Web frontend:

- Registration, sign-in and sign-out; the signed-in username.
- The balance prominently at the top.
- A transfer form (recipient, amount, memo) with a confirmation step that shows the API's messages, for example an insufficient balance.
- The transaction history with direction, other party, amount, memo and date, and paging.

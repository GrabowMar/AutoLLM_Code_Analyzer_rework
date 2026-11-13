Build a small online bank with a JSON API under `/api` and a web frontend served from the same origin. Every account starts with a balance of 1000.00; users send money to other users by username and see their transaction history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`; `POST /api/auth/logout` signs out. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/auth/register`: `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login`: It answers 401 for a wrong username or password, without saying which of the two was wrong.
- `POST /api/auth/logout` signs the current user out. On success it answers 204.
- `GET /api/auth/csrf` returns an anti-forgery token `{"csrf_token": "..."}` for the signed-in user.
- `GET /api/account` returns the signed-in user's `username` and `balance`.
- `POST /api/transfers` sends `amount` from the signed-in user to `to_username` with an optional `memo` and returns the transfer. The balance may never go below 0. On success it answers 201. It answers 400 for an invalid amount, an unknown recipient or a transfer to oneself; 409 when the balance is too low.
- `GET /api/transfers` lists the transfers the user sent or received, newest first, each with `direction` (`in` or `out`), as a paged list. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/transfers/{id}` returns one transfer the user sent or received. It answers 404 for an unknown transfer or one the user is not part of.

Data:

- A transfer is returned as an object with `id`, `to_username`, `amount`, `memo`, `created_at`, `from_username`, `direction`.
- `GET /api/transfers` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a transfer.

Validation (invalid input answers 400 and changes nothing):

- Transfer: `to_username` required, the recipient's username (another existing account); `amount` required, a number above 0 and at most 10000 with at most two decimals; `memo` optional, at most 140 characters.

Rules:

- Every new account starts with a balance of 1000.00; money moves only through transfers.
- Users see only the transfers they sent or received.
- A transfer either happens completely (both balances change) or not at all.

Edge cases:

- Two transfers sent at the same moment must never take the balance below 0.
- Amounts are returned as numbers with two decimals.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration, sign-in and sign-out; the signed-in username.
- The balance prominently at the top.
- A transfer form (recipient, amount, memo) with a confirmation step that shows the API's messages, for example an insufficient balance.
- The transaction history with direction, other party, amount, memo and date, and paging.

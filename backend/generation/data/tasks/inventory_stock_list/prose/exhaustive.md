Build a warehouse inventory app with a JSON API under `/api` and a web frontend. Anyone can read items and their stock movements; signed-in users manage items and record stock received or taken out. Items at or below their reorder level are flagged.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/items` lists items sorted by name as a paged list. `q` matches name or sku case-insensitively as a substring; `location` keeps items at exactly that location; `low_stock=true` keeps only low-stock items; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/items` adds an item with its opening quantity. On success it answers 201. It answers 400 for invalid input; 409 for a sku another item already has.
- `GET /api/items/{id}` returns one item. It answers 404 for an unknown id.
- `PUT /api/items/{id}` replaces an item's `name`, `sku`, `location` and `reorder_level` and leaves its quantity alone. It answers 400 for invalid input; 404 for an unknown id; 409 for a sku another item already has.
- `DELETE /api/items/{id}` deletes an item and its movements. On success it answers 204. It answers 404 for an unknown id.
- `GET /api/items/{id}/movements` lists an item's movements, newest first. It answers 404 for an unknown id.
- `POST /api/items/{id}/movements` records a movement (`delta` positive for stock received, negative for stock taken out) and applies it to the item's quantity. On success it answers 201. It answers 400 for invalid input; 404 for an unknown id; 409 when the quantity would go below 0.

Data:

- An item is returned as an object with `id`, `name`, `sku`, `location`, `quantity`, `reorder_level`, `low_stock`.
- A stock movement is returned as an object with `id`, `item_id`, `delta`, `reason`, `quantity_after`, `created_at`, `username`.
- `GET /api/items` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an item.
- `GET /api/items/{id}/movements` returns `{"items": [...]}`; each item in `items` is a stock movement.

Validation (invalid input answers 400 and changes nothing):

- Item: `name` required, at most 200 characters; `sku` required, at most 40 characters, unique; `location` optional, at most 100 characters; `quantity` optional on create, a whole number of at least 0, default `0`; afterwards changed only by stock movements; `reorder_level` optional, a whole number of at least 0, default `5`.
- Stock movement: `delta` required, a non-zero whole number; `reason` optional, at most 200 characters.

Rules:

- `low_stock` is true when the quantity is at or below `reorder_level`.
- After creation the quantity changes only through stock movements.
- A movement that would take the quantity below 0 is refused and changes nothing.

Edge cases:

- Two concurrent take-outs must never leave a negative quantity.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- An inventory table with low-stock rows highlighted, a search box, a location filter, a 'Low stock only' switch and paging.
- An item page with its movement history and 'Receive' and 'Take out' forms that update the shown quantity.
- Add and edit forms that show the API's validation messages, including a duplicate sku; delete asks for confirmation.

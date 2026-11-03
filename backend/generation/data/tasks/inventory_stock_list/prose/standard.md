Build a warehouse inventory app with a JSON API under `/api` and a web frontend. Anyone can read items and their stock movements; signed-in users manage items and record stock received or taken out. Items at or below their reorder level are flagged.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/items` lists items sorted by name as a paged list (query parameters: `q`, `location`, `low_stock`, `page`, `page_size`).
- `POST /api/items` adds an item with its opening quantity.
- `GET /api/items/{id}` returns one item.
- `PUT /api/items/{id}` replaces an item's `name`, `sku`, `location` and `reorder_level` and leaves its quantity alone.
- `DELETE /api/items/{id}` deletes an item and its movements.
- `GET /api/items/{id}/movements` lists an item's movements, newest first.
- `POST /api/items/{id}/movements` records a movement (`delta` positive for stock received, negative for stock taken out) and applies it to the item's quantity.

Data:

- An item is returned as an object with `id`, `name`, `sku`, `location`, `quantity`, `reorder_level`, `low_stock`.
- A stock movement is returned as an object with `id`, `item_id`, `delta`, `reason`, `quantity_after`, `created_at`, `username`.
- `GET /api/items` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an item.
- `GET /api/items/{id}/movements` returns `{"items": [...]}`; each item in `items` is a stock movement.

Rules:

- `low_stock` is true when the quantity is at or below `reorder_level`.
- After creation the quantity changes only through stock movements.
- A movement that would take the quantity below 0 is refused and changes nothing.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- An inventory table with low-stock rows highlighted, a search box, a location filter, a 'Low stock only' switch and paging.
- An item page with its movement history and 'Receive' and 'Take out' forms that update the shown quantity.
- Add and edit forms that show the API's validation messages, including a duplicate sku; delete asks for confirmation.

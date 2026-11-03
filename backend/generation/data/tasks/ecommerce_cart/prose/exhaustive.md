Build a small shop with a JSON API under `/api` and a web frontend. Anyone can browse products; signed-in users keep a personal cart whose totals the server computes from current prices. The admin adds products and changes prices and stock.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/products` lists active products sorted by name as a paged list. `q` matches name or description case-insensitively as a substring; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/products/{id}` returns one active product. It answers 404 for an unknown id.
- `GET /api/cart` returns the signed-in user's cart: its `items`, oldest first, each with the product `name`, `unit_price` (the current price) and `line_total`, and the `subtotal`. `subtotal` is the sum of the line totals rounded to two decimals.
- `POST /api/cart` adds a `quantity` of a product to the cart; adding a product that is already in the cart increases that item's quantity. A new item answers 201, an increased one 200. On success it answers 201. It answers 400 for invalid input; 404 for an unknown or inactive product; 409 when the quantity would exceed the stock.
- `PATCH /api/cart/{id}` sets a cart item's quantity. It answers 400 for invalid input; 404 for an unknown item or one of another user; 409 when the quantity would exceed the stock.
- `DELETE /api/cart/{id}` removes an item from the cart. On success it answers 204. It answers 404 for an unknown item or one of another user.
- `POST /api/admin/products` adds a product, for the admin. On success it answers 201. It answers 400 for invalid input.
- `PATCH /api/admin/products/{id}` changes a product's fields, including `price`, `stock` and `is_active`, for the admin. It answers 400 for invalid input; 404 for an unknown id.

Data:

- A product is returned as an object with `id`, `name`, `description`, `price`, `stock`, `is_active`.
- A cart item is returned as an object with `id`, `product_id`, `quantity`, `name`, `unit_price`, `line_total`.
- `GET /api/products` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a product.
- `GET /api/cart` returns `{"items": [...], "subtotal": n}`; each item in `items` is a cart item.

Validation (invalid input answers 400 and changes nothing):

- Product: `name` required, at most 120 characters; `description` optional, at most 1000 characters; `price` required, a number above 0 with two decimals; `stock` required, a whole number of at least 0; `is_active` optional, `true` or `false`, default `true`.
- Cart item: `product_id` required; `quantity` required, a whole number of at least 1 and at most the product's stock.

Rules:

- Each user sees and changes only their own cart; another user's cart item behaves as if it did not exist.
- Prices come from the catalogue: the cart always uses the product's current price, never a price sent by the client.
- Adding to the cart does not change stock. An inactive product is hidden from the catalogue and its cart items are left out of the cart and the subtotal.
- On first start the catalogue holds at least 8 products with a stock of at least 10.

Edge cases:

- A cart item's quantity may never exceed the product's current stock.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A product grid with name, price, a placeholder image tile and an 'Out of stock' badge, a search box and paging.
- A cart badge with the number of items and a cart panel with a quantity stepper, a remove button, line totals and the subtotal; rejected changes show the API's message.
- For the admin: a product form and inline editing of price, stock and active status.

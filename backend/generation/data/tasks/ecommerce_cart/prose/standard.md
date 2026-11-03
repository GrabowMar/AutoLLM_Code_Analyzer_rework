Build a small shop with a JSON API under `/api` and a web frontend. Anyone can browse products; signed-in users keep a personal cart whose totals the server computes from current prices. The admin adds products and changes prices and stock.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/products` lists active products sorted by name as a paged list (query parameters: `q`, `page`, `page_size`).
- `GET /api/products/{id}` returns one active product.
- `GET /api/cart` returns the signed-in user's cart: its `items`, oldest first, each with the product `name`, `unit_price` (the current price) and `line_total`, and the `subtotal`.
- `POST /api/cart` adds a `quantity` of a product to the cart; adding a product that is already in the cart increases that item's quantity.
- `PATCH /api/cart/{id}` sets a cart item's quantity.
- `DELETE /api/cart/{id}` removes an item from the cart.
- `POST /api/admin/products` adds a product, for the admin.
- `PATCH /api/admin/products/{id}` changes a product's fields, including `price`, `stock` and `is_active`, for the admin.

Data:

- A product is returned as an object with `id`, `name`, `description`, `price`, `stock`, `is_active`.
- A cart item is returned as an object with `id`, `product_id`, `quantity`, `name`, `unit_price`, `line_total`.
- `GET /api/products` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a product.
- `GET /api/cart` returns `{"items": [...], "subtotal": n}`; each item in `items` is a cart item.

Rules:

- Each user sees and changes only their own cart; another user's cart item behaves as if it did not exist.
- Prices come from the catalogue: the cart always uses the product's current price, never a price sent by the client.
- Adding to the cart does not change stock. An inactive product is hidden from the catalogue and its cart items are left out of the cart and the subtotal.
- On first start the catalogue holds at least 8 products with a stock of at least 10.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A product grid with name, price, a placeholder image tile and an 'Out of stock' badge, a search box and paging.
- A cart badge with the number of items and a cart panel with a quantity stepper, a remove button, line totals and the subtotal; rejected changes show the API's message.
- For the admin: a product form and inline editing of price, stock and active status.

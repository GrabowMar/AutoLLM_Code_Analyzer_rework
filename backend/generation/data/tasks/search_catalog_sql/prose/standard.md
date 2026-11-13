Build a product catalogue search with a JSON API under `/api` and a web frontend. The catalogue is read-only and needs no accounts: on first start the app holds at least 60 products across at least 5 categories and 8 brands. Visitors search with text, combine filters, sort the results and see facet counts.

API:

- `GET /api/products` searches the catalogue and returns a paged list plus `facets`: the number of matching products per `category` and per `brand` (query parameters: `q`, `category`, `brand`, `min_price`, `max_price`, `min_rating`, `in_stock`, `sort`, `page`, `page_size`).
- `GET /api/products/{id}` returns one product.
- `GET /api/categories` lists every category with its product count, sorted by name.
- `GET /api/suggest` returns up to 8 product names starting with `prefix` (case-insensitive), for autocompletion (query parameters: `prefix`).

Data:

- A product is returned as an object with `id`, `name`, `description`, `category`, `brand`, `price`, `rating`, `in_stock`, `released_on`.
- `GET /api/products` returns `{"items": [...], "total": n, "page": n, "page_size": n, "facets": {...}}`; each item in `items` is a product.
- `GET /api/categories` returns `{"items": [...]}`; each item in `items` is an object with `name` and `count`.
- `GET /api/suggest` returns `{"items": [...]}`; each item in `items` is an object with `id` and `name`.

Rules:

- Every filter combines with every other (all must hold).
- Search text is treated as text: characters such as quotes, `%`, `_` and backslashes match themselves.

Web frontend:

- A search box with autocompletion, a results grid (name, brand, price, rating, stock badge) and paging.
- A filter sidebar with category and brand checkboxes showing facet counts, a price range, a minimum rating and an 'In stock' switch; the URL reflects the current search so it can be shared.
- A sort selector.
- A product page.

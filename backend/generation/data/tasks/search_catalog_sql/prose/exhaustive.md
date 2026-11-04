Build a product catalogue search with a JSON API under `/api` and a web frontend. The catalogue is read-only and needs no accounts: on first start the app holds at least 60 products across at least 5 categories and 8 brands. Visitors search with text, combine filters, sort the results and see facet counts.

API:

- `GET /api/products` searches the catalogue and returns a paged list plus `facets`: the number of matching products per `category` and per `brand`. Facet counts apply every filter except the facet's own, so a selected category still shows the counts of the other categories. `q` matches every word in name or description case-insensitively (all words must match); `category` keeps one category (exact); `brand` may be repeated and keeps products of any listed brand; `min_price` and `max_price` keep prices within that inclusive range; `max_price` is inclusive; a `min_price` above `max_price` answers 400; `min_rating` (0 to 5) keeps products rated at least that; `in_stock=true` keeps products in stock; `sort` is one of `relevance` (default; name matches before description matches, then by name), `price`, `-price`, `name`, `-rating`, `-released_on`; anything else answers 400; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 24, at most 96). It answers 400 for an unknown sort, a malformed number or an inverted range.
- `GET /api/products/{id}` returns one product. It answers 404 for an unknown id.
- `GET /api/categories` lists every category with its product count, sorted by name.
- `GET /api/suggest` returns up to 8 product names starting with `prefix` (case-insensitive), for autocompletion. `prefix` is required and at most 50 characters. It answers 400 without `prefix` or for one over 50 characters.

Data:

- A product is returned as an object with `id`, `name`, `description`, `category`, `brand`, `price`, `rating`, `in_stock`, `released_on`.
- `GET /api/products` returns `{"items": [...], "total": n, "page": n, "page_size": n, "facets": {...}}`; each item in `items` is a product.
- `GET /api/categories` returns `{"items": [...]}`; each item in `items` is an object with `name` and `count`.
- `GET /api/suggest` returns `{"items": [...]}`; each item in `items` is an object with `id` and `name`.

Rules:

- Every filter combines with every other (all must hold).
- Search text is treated as text: characters such as quotes, `%`, `_` and backslashes match themselves.

Edge cases:

- A search without parameters lists every product.
- A page past the end returns an empty `items` list with the correct `total` and facets.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- A search box with autocompletion, a results grid (name, brand, price, rating, stock badge) and paging.
- A filter sidebar with category and brand checkboxes showing facet counts, a price range, a minimum rating and an 'In stock' switch; the URL reflects the current search so it can be shared.
- A sort selector.
- A product page.

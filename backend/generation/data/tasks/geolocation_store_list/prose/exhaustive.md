Build a store finder with a JSON API under `/api` and a web frontend. Anyone can browse stores and search near a location; the admin maintains the store directory.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/stores` lists stores sorted by name; with `lat` and `lng` it keeps the stores within `radius` kilometres, nearest first, with their `distance_km`. `distance_km` is the great-circle distance rounded to one decimal, and `null` without a location; `lat` without `lng` (or the reverse) or an out-of-range value answers 400. `q` matches name or address case-insensitively as a substring; `lat` is the latitude of the search point (-90 to 90); `lng` is the longitude of the search point (-180 to 180); `radius` is the search radius in kilometres (default 25, at most 500). It answers 400 for invalid input.
- `GET /api/stores/{id}` returns one store. It answers 404 for an unknown id.
- `POST /api/admin/stores` adds a store, for the admin. On success it answers 201. It answers 400 for invalid input.
- `PUT /api/admin/stores/{id}` replaces a store's fields, for the admin; an omitted `phone` is cleared. It answers 400 for invalid input; 404 for an unknown id.
- `DELETE /api/admin/stores/{id}` deletes a store, for the admin. On success it answers 204. It answers 404 for an unknown id.

Data:

- A store is returned as an object with `id`, `name`, `address`, `phone`, `latitude`, `longitude`, `distance_km`.
- `GET /api/stores` returns `{"items": [...]}`; each item in `items` is a store.

Validation (invalid input answers 400 and changes nothing):

- Store: `name` required, at most 100 characters; `address` required, at most 255 characters; `phone` optional, at most 30 characters; `latitude` required, a number from -90 to 90; `longitude` required, a number from -180 to 180.

Rules:

- Only the admin changes the directory; everyone may read it.
- On first start the directory holds 8 sample stores across a few cities.

Edge cases:

- A radius of 0 or below answers 400.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- A sign-in form for the admin.
- The store list with name, address and phone and a search box.
- A 'Use my location' button that lists nearby stores with their distance and a radius selector; if permission is denied the plain list stays with a short note.
- A simple plot that places the listed stores (and the visitor, when known) by latitude and longitude, drawn by the page itself without external map tiles.
- For the admin: add and edit forms that show the API's validation messages, and delete after confirmation.

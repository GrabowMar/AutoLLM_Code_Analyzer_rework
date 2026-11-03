Build a store finder with a JSON API under `/api` and a web frontend. Anyone can browse stores and search near a location; the admin maintains the store directory.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/stores` lists stores sorted by name; with `lat` and `lng` it keeps the stores within `radius` kilometres, nearest first, with their `distance_km` (query parameters: `q`, `lat`, `lng`, `radius`).
- `GET /api/stores/{id}` returns one store.
- `POST /api/admin/stores` adds a store, for the admin.
- `PUT /api/admin/stores/{id}` replaces a store's fields, for the admin; an omitted `phone` is cleared.
- `DELETE /api/admin/stores/{id}` deletes a store, for the admin.

Data:

- A store is returned as an object with `id`, `name`, `address`, `phone`, `latitude`, `longitude`, `distance_km`.
- `GET /api/stores` returns `{"items": [...]}`; each item in `items` is a store.

Rules:

- Only the admin changes the directory; everyone may read it.
- On first start the directory holds 8 sample stores across a few cities.

Web frontend:

- A sign-in form for the admin.
- The store list with name, address and phone and a search box.
- A 'Use my location' button that lists nearby stores with their distance and a radius selector; if permission is denied the plain list stays with a short note.
- A simple plot that places the listed stores (and the visitor, when known) by latitude and longitude, drawn by the page itself without external map tiles.
- For the admin: add and edit forms that show the API's validation messages, and delete after confirmation.

Build a shared customer directory with a JSON API under `/api` and a web frontend. The directory is private to signed-in team members, who may all read and change every customer and log their interactions (calls, emails, meetings).

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/customers` lists customers sorted by name as a paged list (query parameters: `q`, `company`, `page`, `page_size`).
- `POST /api/customers` adds a customer.
- `GET /api/customers/{id}` returns one customer.
- `PUT /api/customers/{id}` replaces a customer's fields; an omitted optional field is cleared.
- `DELETE /api/customers/{id}` deletes a customer and its interactions.
- `GET /api/customers/{id}/interactions` lists a customer's interactions, newest first.
- `POST /api/customers/{id}/interactions` logs an interaction for the signed-in user.

Data:

- A customer is returned as an object with `id`, `name`, `email`, `phone`, `company`, `notes`, `created_at`, `interaction_count`, `last_contacted_at`.
- An interaction is returned as an object with `id`, `customer_id`, `kind`, `summary`, `occurred_at`, `logged_by`.
- `GET /api/customers` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a customer.
- `GET /api/customers/{id}/interactions` returns `{"items": [...]}`; each item in `items` is an interaction.

Rules:

- Only signed-in team members may read or change anything; every member may change every customer.
- `last_contacted_at` is the time of the customer's latest interaction, or `null` without one.

Web frontend:

- A sign-in screen with a short description of the directory, and a registration form.
- A customer table with name, email, phone, company and last contacted date, a search box, a company filter and paging.
- A customer page with edit and delete actions, the interaction log (kind, summary, date, who logged it) and a form to log a new one.
- Add and edit forms that show the API's validation messages, including the duplicate-email message.

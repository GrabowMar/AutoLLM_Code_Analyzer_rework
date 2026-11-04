Build a shared customer directory with a JSON API under `/api` and a web frontend. The directory is private to signed-in team members, who may all read and change every customer and log their interactions (calls, emails, meetings).

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/customers` lists customers sorted by name as a paged list. `q` matches name, email or company case-insensitively as a substring; `company` keeps customers of exactly that company, ignoring case; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/customers` adds a customer. On success it answers 201. It answers 400 for invalid input; 409 for an email another customer already has.
- `GET /api/customers/{id}` returns one customer. It answers 404 for an unknown id.
- `PUT /api/customers/{id}` replaces a customer's fields; an omitted optional field is cleared. It answers 400 for invalid input; 404 for an unknown id; 409 for an email another customer already has.
- `DELETE /api/customers/{id}` deletes a customer and its interactions. On success it answers 204. It answers 404 for an unknown id.
- `GET /api/customers/{id}/interactions` lists a customer's interactions, newest first. It answers 404 for an unknown id.
- `POST /api/customers/{id}/interactions` logs an interaction for the signed-in user. On success it answers 201. It answers 400 for invalid input; 404 for an unknown id.

Data:

- A customer is returned as an object with `id`, `name`, `email`, `phone`, `company`, `notes`, `created_at`, `interaction_count`, `last_contacted_at`.
- An interaction is returned as an object with `id`, `customer_id`, `kind`, `summary`, `occurred_at`, `logged_by`.
- `GET /api/customers` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a customer.
- `GET /api/customers/{id}/interactions` returns `{"items": [...]}`; each item in `items` is an interaction.

Validation (invalid input answers 400 and changes nothing):

- Customer: `name` required, at most 100 characters; `email` required, an email address of at most 255 characters, unique ignoring case; `phone` optional, at most 30 characters; `company` optional, at most 100 characters; `notes` optional, at most 2000 characters.
- Interaction: `kind` required, one of `call`, `email`, `meeting`; `summary` required, at most 500 characters; `occurred_at` optional, an ISO 8601 date-time; default now.

Rules:

- Only signed-in team members may read or change anything; every member may change every customer.
- `last_contacted_at` is the time of the customer's latest interaction, or `null` without one.

Edge cases:

- Emails are compared ignoring case.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- A sign-in screen with a short description of the directory, and a registration form.
- A customer table with name, email, phone, company and last contacted date, a search box, a company filter and paging.
- A customer page with edit and delete actions, the interaction log (kind, summary, date, who logged it) and a form to log a new one.
- Add and edit forms that show the API's validation messages, including the duplicate-email message.

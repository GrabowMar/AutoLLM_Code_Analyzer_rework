Build an XML well-formedness checker with a JSON API under `/api` and a web frontend. Anyone can check whether a document is well-formed (syntax only, no schema validation) and see where the first error is; signed-in users can save checks to a private history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/validate/xml` checks `xml` and returns `valid`, `error`, `line`, `column` and `root_element`; nothing is stored.
- `POST /api/validations` checks the same way and saves the result with `xml_preview` (the first 200 characters) to the user's history.
- `GET /api/validations` lists the user's saved checks, newest first, as a paged list (query parameters: `valid`, `page`, `page_size`).
- `DELETE /api/validations/{id}` deletes one of the user's saved checks.

Data:

- A saved check is returned as an object with `id`, `xml_preview`, `valid`, `error`, `line`, `column`, `root_element`, `created_at`.
- `GET /api/validations` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a saved check.

Rules:

- The checker decides well-formedness only; documents are never fetched from or resolved against anything outside the submitted text.
- Each user sees and deletes only their own saved checks.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A text area (with file loading) and a 'Check' button; the result shows a green 'Well-formed' badge with the root element, or the error with its position, and highlights that line.
- For signed-in users: a 'Save' switch and the saved checks with a valid/invalid filter, delete buttons and paging.

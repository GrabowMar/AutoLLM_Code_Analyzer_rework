Build an XML well-formedness checker with a JSON API under `/api` and a web frontend. Anyone can check whether a document is well-formed (syntax only, no schema validation) and see where the first error is; signed-in users can save checks to a private history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/validate/xml` checks `xml` and returns `valid`, `error`, `line`, `column` and `root_element`; nothing is stored. A well-formed document has `error`, `line` and `column` `null` and `root_element` set; otherwise `error` describes the first problem, `line` and `column` give its 1-based position (`null` when unknown) and `root_element` is `null`. A document that is not well-formed is still a successful check (200 with `valid: false`). It answers 400 for a missing, empty or over-long document.
- `POST /api/validations` checks the same way and saves the result with `xml_preview` (the first 200 characters) to the user's history. On success it answers 201. It answers 400 for invalid input.
- `GET /api/validations` lists the user's saved checks, newest first, as a paged list. `valid=true` or `valid=false` keeps checks with that result; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `DELETE /api/validations/{id}` deletes one of the user's saved checks. On success it answers 204. It answers 404 for an unknown check or one of another user.

Data:

- A saved check is returned as an object with `id`, `xml_preview`, `valid`, `error`, `line`, `column`, `root_element`, `created_at`.
- `GET /api/validations` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a saved check.

Validation (invalid input answers 400 and changes nothing):

- Saved check: `xml` required, at most 200000 characters.

Rules:

- The checker decides well-formedness only; documents are never fetched from or resolved against anything outside the submitted text.
- Each user sees and deletes only their own saved checks.

Edge cases:

- A document with a DOCTYPE is checked like any other; an entity that cannot be resolved locally is reported as an error, never fetched.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A text area (with file loading) and a 'Check' button; the result shows a green 'Well-formed' badge with the root element, or the error with its position, and highlights that line.
- For signed-in users: a 'Save' switch and the saved checks with a valid/invalid filter, delete buttons and paging.

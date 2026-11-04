Build a base64 converter with a JSON API under `/api` and a web frontend. Anyone can encode UTF-8 text to base64 and decode it back; signed-in users can also save conversions to a private history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/base64/encode` encodes `text` as UTF-8 to standard base64 with padding and returns `encoded`; nothing is stored.
- `POST /api/base64/decode` decodes `encoded` and returns the UTF-8 `text`; nothing is stored.
- `POST /api/conversions` converts like the endpoints above (`direction` `encode` or `decode`, `input`), saves the conversion to the user's history and returns the full `output`.
- `GET /api/conversions` lists the user's saved conversions, newest first, as a paged list (query parameters: `page`, `page_size`).
- `DELETE /api/conversions/{id}` deletes one of the user's saved conversions.

Data:

- A conversion is returned as an object with `id`, `direction`, `input_preview`, `output_preview`, `created_at`.
- `POST /api/conversions` returns the conversion with `output` as well.
- `GET /api/conversions` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a conversion.

Rules:

- Each user sees and deletes only their own history.
- Encoding takes the text as UTF-8; empty text is allowed.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Two text areas with 'Encode' and 'Decode' buttons, a copy button and the API's error message for invalid input.
- For signed-in users: a 'Save to history' switch and the history list with delete buttons and paging.

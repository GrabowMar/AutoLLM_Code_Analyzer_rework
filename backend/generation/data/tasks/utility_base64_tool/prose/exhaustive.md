Build a base64 converter with a JSON API under `/api` and a web frontend. Anyone can encode UTF-8 text to base64 and decode it back; signed-in users can also save conversions to a private history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/base64/encode` encodes `text` as UTF-8 to standard base64 with padding and returns `encoded`; nothing is stored. It answers 400 for invalid input.
- `POST /api/base64/decode` decodes `encoded` and returns the UTF-8 `text`; nothing is stored. Surrounding whitespace and line breaks are ignored and missing padding is accepted; input that is not valid base64 or does not decode to valid UTF-8 answers 400. It answers 400 for invalid input.
- `POST /api/conversions` converts like the endpoints above (`direction` `encode` or `decode`, `input`), saves the conversion to the user's history and returns the full `output`. Only the first 200 characters of the input and of the output are stored. On success it answers 201. It answers 400 for invalid input.
- `GET /api/conversions` lists the user's saved conversions, newest first, as a paged list. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `DELETE /api/conversions/{id}` deletes one of the user's saved conversions. On success it answers 204. It answers 404 for an unknown conversion or one of another user.

Data:

- A conversion is returned as an object with `id`, `direction`, `input_preview`, `output_preview`, `created_at`.
- `POST /api/conversions` returns the conversion with `output` as well.
- `GET /api/conversions` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a conversion.

Validation (invalid input answers 400 and changes nothing):

- Conversion: `direction` required, one of `encode`, `decode`; `input` required; text to encode is at most 100000 characters, base64 to decode at most 140000.

Rules:

- Each user sees and deletes only their own history.
- Encoding takes the text as UTF-8; empty text is allowed.

Edge cases:

- Decoding the encoding of any text gives the text back unchanged, including non-ASCII characters.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- Two text areas with 'Encode' and 'Decode' buttons, a copy button and the API's error message for invalid input.
- For signed-in users: a 'Save to history' switch and the history list with delete buttons and paging.

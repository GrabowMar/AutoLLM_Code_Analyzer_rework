Build a URL shortener with a JSON API under `/api` and a web frontend. Signed-in users shorten long addresses; anyone can follow a short link, which redirects to the original address and counts the click. The admin moderates links.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/links` shortens an address for the signed-in user and returns the link with its `short_code` and `short_url` (the path `/r/<short_code>` on this app). On success it answers 201. It answers 400 for a missing, malformed or over-long address.
- `GET /api/links` lists active links, newest first, as a paged list. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/links/{id}` returns one active link with its click count. It answers 404 for an unknown or disabled link.
- `DELETE /api/links/{id}` deletes one of the signed-in user's links permanently. On success it answers 204. It answers 403 for a link of another user; 404 for an unknown id.
- `GET /api/admin/links` lists every link, active or not, with its owner, for the admin. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/admin/links/{id}/toggle` disables an active link or re-enables a disabled one, for the admin. It answers 404 for an unknown id.

Data:

- A link is returned as an object with `id`, `original_url`, `short_code`, `click_count`, `created_at`, `short_url`, `owner`.
- `GET /api/links` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a link.
- `GET /api/admin/links` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a link.

Validation (invalid input answers 400 and changes nothing):

- Link: `original_url` required, an absolute `http` or `https` URL, at most 2048 characters; `short_code` set by the server: unique, 6 characters from `A-Z`, `a-z` and `0-9`.

Rules:

- Only the user who created a link may delete it.
- A disabled link is left out of the public list, answers 404 on its endpoints and no longer redirects; the admin still sees it.
- Only `http` and `https` addresses are accepted.
- `GET /r/{code}` (outside `/api`, taking precedence over the frontend's own routes) answers 302 with `Location` set to the original address and adds 1 to the link's click count; an unknown or disabled code answers 404.

Edge cases:

- A randomly drawn code that is already taken is drawn again; codes are never reused for another address.
- The redirect answers with the stored address exactly as given.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A form to shorten an address that shows the full short address with a copy button, or the API's validation message.
- The recent links with short address, original address (truncated, full value on hover), click count and date, with paging; a delete button with confirmation on the user's own links.
- For the admin: all links with their owners and a button to disable or re-enable each.

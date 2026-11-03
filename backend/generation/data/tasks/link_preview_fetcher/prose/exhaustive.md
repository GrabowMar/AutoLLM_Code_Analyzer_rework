Build a link bookmarking app with previews, with a JSON API under `/api` and a web frontend. Signed-in users save web links; the server fetches each page and stores a preview built from its title, description and image (from `<title>`, `<meta name="description">` and the Open Graph `og:title`, `og:description` and `og:image` tags).

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/previews` fetches the page at `url`, stores the preview and returns it. Only `http` and `https` addresses are fetched; up to 3 redirects are followed, the fetch gives up after 5 seconds and reads at most the first 1 MB. A page that cannot be fetched is still saved, with `status_code` `null` and empty fields. On success it answers 201. It answers 400 for a missing, malformed or non-http(s) address.
- `GET /api/previews` lists the user's previews, newest first, as a paged list. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/previews/{id}` returns one preview. It answers 404 for an unknown preview or one of another user.
- `POST /api/previews/{id}/refresh` fetches the page again and updates the preview. It answers 404 for an unknown preview or one of another user.
- `DELETE /api/previews/{id}` deletes a preview. On success it answers 204. It answers 404 for an unknown preview or one of another user.

Data:

- A preview is returned as an object with `id`, `url`, `final_url`, `status_code`, `title`, `description`, `image_url`, `fetched_at`.
- `GET /api/previews` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a preview.

Validation (invalid input answers 400 and changes nothing):

- Preview: `url` required, an absolute `http` or `https` URL, at most 2048 characters.

Rules:

- Each user sees and changes only their own previews; another user's preview behaves as if it did not exist.
- Values taken from fetched pages are plain text.

Edge cases:

- Open Graph values win over `<title>` and `<meta name="description">` when both exist.
- A relative `og:image` is resolved against the page's final address.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A field to paste a link and an 'Add' button with a loading state.
- Preview cards with image, title, description and the site's host, linking to the page, with refresh and delete buttons and paging.

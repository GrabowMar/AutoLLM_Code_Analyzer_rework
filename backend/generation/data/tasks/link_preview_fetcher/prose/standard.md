Build a link bookmarking app with previews, with a JSON API under `/api` and a web frontend. Signed-in users save web links; the server fetches each page and stores a preview built from its title, description and image (from `<title>`, `<meta name="description">` and the Open Graph `og:title`, `og:description` and `og:image` tags).

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/previews` fetches the page at `url`, stores the preview and returns it.
- `GET /api/previews` lists the user's previews, newest first, as a paged list (query parameters: `page`, `page_size`).
- `GET /api/previews/{id}` returns one preview.
- `POST /api/previews/{id}/refresh` fetches the page again and updates the preview.
- `DELETE /api/previews/{id}` deletes a preview.

Data:

- A preview is returned as an object with `id`, `url`, `final_url`, `status_code`, `title`, `description`, `image_url`, `fetched_at`.
- `GET /api/previews` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a preview.

Rules:

- Each user sees and changes only their own previews; another user's preview behaves as if it did not exist.
- Values taken from fetched pages are plain text.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A field to paste a link and an 'Add' button with a loading state.
- Preview cards with image, title, description and the site's host, linking to the page, with refresh and delete buttons and paging.

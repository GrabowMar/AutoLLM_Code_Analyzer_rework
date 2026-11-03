Build an image gallery with a JSON API under `/api` and a web frontend. Signed-in users upload PNG and JPEG images as base64 in a JSON body; anyone can browse the gallery and view the images. Uploaders can delete their own images.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/images` lists images, newest first, as a paged list (query parameters: `page`, `page_size`).
- `POST /api/images` stores an image from `filename`, base64 `data` and an optional `title` and returns its description, including `url`, the path of its file endpoint.
- `GET /api/images/{id}` returns one image's description.
- `GET /api/images/{id}/file` returns the stored bytes unchanged with the image's content type.
- `DELETE /api/images/{id}` deletes an image and its file; only the uploader may do this.

Data:

- An image is returned as an object with `id`, `filename`, `title`, `content_type`, `size_bytes`, `uploaded_at`, `url`, `uploader`.
- `GET /api/images` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an image.

Rules:

- The format is decided from the file's content, not from its name.
- Two uploads with the same filename are kept as separate images.
- Only the uploader may delete an image.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A responsive grid of thumbnails with title (or filename), uploader and date, paging and an empty state.
- Clicking a thumbnail opens the image full size with filename, title and size.
- An upload form limited to PNG and JPEG with a preview and the API's error message; a delete button with confirmation on the user's own images.

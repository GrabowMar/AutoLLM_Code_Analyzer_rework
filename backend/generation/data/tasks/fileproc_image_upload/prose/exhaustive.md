Build an image gallery with a JSON API under `/api` and a web frontend. Signed-in users upload PNG and JPEG images as base64 in a JSON body; anyone can browse the gallery and view the images. Uploaders can delete their own images.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/images` lists images, newest first, as a paged list. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 24, at most 100).
- `POST /api/images` stores an image from `filename`, base64 `data` and an optional `title` and returns its description, including `url`, the path of its file endpoint. On success it answers 201. It answers 400 for data that is not valid base64, empty, or not PNG or JPEG; 413 for a file over 5 MB.
- `GET /api/images/{id}` returns one image's description. It answers 404 for an unknown id.
- `GET /api/images/{id}/file` returns the stored bytes unchanged with the image's content type. It answers 404 for an unknown id.
- `DELETE /api/images/{id}` deletes an image and its file; only the uploader may do this. On success it answers 204. It answers 403 for an image of another user; 404 for an unknown id.

Data:

- An image is returned as an object with `id`, `filename`, `title`, `content_type`, `size_bytes`, `uploaded_at`, `url`, `uploader`.
- `GET /api/images` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is an image.

Validation (invalid input answers 400 and changes nothing):

- Image: `filename` required, at most 255 characters; `title` optional, at most 100 characters; `data` required, the file's bytes, base64-encoded; PNG or JPEG only, at most 5 MB after decoding (413 above).

Rules:

- The format is decided from the file's content, not from its name.
- Two uploads with the same filename are kept as separate images.
- Only the uploader may delete an image.

Edge cases:

- The stored file is served exactly as uploaded.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A responsive grid of thumbnails with title (or filename), uploader and date, paging and an empty state.
- Clicking a thumbnail opens the image full size with filename, title and size.
- An upload form limited to PNG and JPEG with a preview and the API's error message; a delete button with confirmation on the user's own images.

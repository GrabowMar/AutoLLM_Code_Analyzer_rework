Build a file sharing app with a JSON API under `/api` and a web frontend. Signed-in users upload files with a multipart form, list and download their own files, delete them, and create share links that let anyone download a file without signing in.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/files` stores a file uploaded as `multipart/form-data` (field `file`, optional field `description`) and returns its description. `filename` is the uploaded file's name without any directory part. On success it answers 201. It answers 400 for a request without a file or with an empty file; 413 for a file over 10 MB.
- `GET /api/files` lists the user's files, newest first, as a paged list. `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `GET /api/files/{id}` returns one file's description. It answers 404 for an unknown file or one of another user.
- `GET /api/files/{id}/download` returns the stored bytes unchanged as an attachment named after the original filename. It answers 404 for an unknown file or one of another user.
- `DELETE /api/files/{id}` deletes a file, its stored bytes and its share links. On success it answers 204. It answers 404 for an unknown file or one of another user.
- `POST /api/files/{id}/shares` creates a share link for the file and returns its `token` and `url` (`/api/shared/<token>`). On success it answers 201. It answers 404 for an unknown file or one of another user.
- `DELETE /api/files/{id}/shares/{token}` revokes a share link. On success it answers 204. It answers 404 for an unknown file, link or a file of another user.
- `GET /api/shared/{token}` downloads the shared file without signing in. It answers 404 for an unknown or revoked link.

Data:

- A file is returned as an object with `id`, `description`, `filename`, `size_bytes`, `content_type`, `uploaded_at`, `download_url`.
- `GET /api/files` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a file.

Validation (invalid input answers 400 and changes nothing):

- File: `file` required, the uploaded file (form field `file`), 1 byte to 10 MB (413 above); `description` optional, at most 300 characters.

Rules:

- Each user sees, downloads and deletes only their own files; another user's file behaves as if it did not exist.
- A share link works for anyone who has it until it is revoked or the file is deleted.
- Downloads return the bytes exactly as uploaded.

Edge cases:

- Two uploads with the same filename are kept as separate files.
- A share token cannot be guessed from the file id or from other tokens.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- An upload area with a file picker or drag and drop, an optional description and a progress bar; rejected uploads show the API's message.
- The user's files with name, size, type and date, download and delete buttons (delete with confirmation) and paging.
- A 'Share' action that creates a link with a copy button and lists the file's links with a revoke button.

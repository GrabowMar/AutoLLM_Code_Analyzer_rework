Build a file sharing app with a JSON API under `/api` and a web frontend. Signed-in users upload files with a multipart form, list and download their own files, delete them, and create share links that let anyone download a file without signing in.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/files` stores a file uploaded as `multipart/form-data` (field `file`, optional field `description`) and returns its description.
- `GET /api/files` lists the user's files, newest first, as a paged list (query parameters: `page`, `page_size`).
- `GET /api/files/{id}` returns one file's description.
- `GET /api/files/{id}/download` returns the stored bytes unchanged as an attachment named after the original filename.
- `DELETE /api/files/{id}` deletes a file, its stored bytes and its share links.
- `POST /api/files/{id}/shares` creates a share link for the file and returns its `token` and `url` (`/api/shared/<token>`).
- `DELETE /api/files/{id}/shares/{token}` revokes a share link.
- `GET /api/shared/{token}` downloads the shared file without signing in.

Data:

- A file is returned as an object with `id`, `description`, `filename`, `size_bytes`, `content_type`, `uploaded_at`, `download_url`.
- `GET /api/files` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a file.

Rules:

- Each user sees, downloads and deletes only their own files; another user's file behaves as if it did not exist.
- A share link works for anyone who has it until it is revoked or the file is deleted.
- Downloads return the bytes exactly as uploaded.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- An upload area with a file picker or drag and drop, an optional description and a progress bar; rejected uploads show the API's message.
- The user's files with name, size, type and date, download and delete buttons (delete with confirmation) and paging.
- A 'Share' action that creates a link with a copy button and lists the file's links with a revoke button.

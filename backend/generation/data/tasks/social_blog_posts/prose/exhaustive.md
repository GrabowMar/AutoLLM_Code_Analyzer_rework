Build a multi-author blog with a JSON API under `/api` and a web frontend. Signed-in users publish posts with tags; anyone reads them. Authors edit and delete their own posts, and the admin can take any post down.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/posts` lists posts, newest first, as a paged list. `q` matches title or content case-insensitively as a substring; `tag` keeps posts carrying that tag; `author` keeps posts by that username; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 10, at most 50).
- `POST /api/posts` publishes a post by the signed-in user. On success it answers 201. It answers 400 for invalid input.
- `GET /api/posts/{id}` returns one post. It answers 404 for an unknown id.
- `PUT /api/posts/{id}` replaces one of the user's posts; omitted tags are cleared. It answers 400 for invalid input; 403 for a post of another user; 404 for an unknown id.
- `DELETE /api/posts/{id}` deletes one of the user's posts. On success it answers 204. It answers 403 for a post of another user; 404 for an unknown id.
- `POST /api/admin/posts/{id}/toggle` takes a post down or restores it, for the admin. It answers 404 for an unknown id.

Data:

- A post is returned as an object with `id`, `title`, `content`, `tags`, `created_at`, `updated_at`, `author`, `excerpt`.
- `GET /api/posts` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a post.

Validation (invalid input answers 400 and changes nothing):

- Post: `title` required, at most 200 characters; `content` required, at most 50000 characters; `tags` optional, at most 10 tags; each is trimmed and lower-cased, 1 to 30 characters, duplicates dropped.

Rules:

- Anyone may read posts; only the author may change or delete one.
- `excerpt` is the content's first 200 characters with line breaks collapsed to spaces; `updated_at` changes on every update.
- A taken-down post is left out of the list and answers 404 for everyone but the admin.

Edge cases:

- Tags are compared after trimming and lower-casing.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A post list with title, author, date, excerpt and tags, a search box, tag and author filters and paging.
- A post page that shows the content as plain text with paragraphs kept.
- An editor for new and own posts with a tag input, showing the API's validation messages; delete with confirmation.
- For the admin: a take-down button on every post.

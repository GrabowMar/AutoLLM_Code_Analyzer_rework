Build a multi-author blog with a JSON API under `/api` and a web frontend. Signed-in users publish posts with tags; anyone reads them. Authors edit and delete their own posts, and the admin can take any post down.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/posts` lists posts, newest first, as a paged list (query parameters: `q`, `tag`, `author`, `page`, `page_size`).
- `POST /api/posts` publishes a post by the signed-in user.
- `GET /api/posts/{id}` returns one post.
- `PUT /api/posts/{id}` replaces one of the user's posts; omitted tags are cleared.
- `DELETE /api/posts/{id}` deletes one of the user's posts.
- `POST /api/admin/posts/{id}/toggle` takes a post down or restores it, for the admin.

Data:

- A post is returned as an object with `id`, `title`, `content`, `tags`, `created_at`, `updated_at`, `author`, `excerpt`.
- `GET /api/posts` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a post.

Rules:

- Anyone may read posts; only the author may change or delete one.
- `excerpt` is the content's first 200 characters with line breaks collapsed to spaces; `updated_at` changes on every update.
- A taken-down post is left out of the list and answers 404 for everyone but the admin.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A post list with title, author, date, excerpt and tags, a search box, tag and author filters and paging.
- A post page that shows the content as plain text with paragraphs kept.
- An editor for new and own posts with a tag input, showing the API's validation messages; delete with confirmation.
- For the admin: a take-down button on every post.

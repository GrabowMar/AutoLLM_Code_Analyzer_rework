Build a multi-author blog. Signed-in users publish posts (title, plain-text content, tags) and edit or delete their own; anyone browses posts by text, tag or author. The admin can take any post down.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/posts, POST /api/posts, GET /api/posts/{id}, PUT /api/posts/{id}, DELETE /api/posts/{id}, POST /api/admin/posts/{id}/toggle.

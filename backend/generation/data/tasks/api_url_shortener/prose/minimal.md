Build a URL shortener. Signed-in users turn long web addresses into short links; anyone following a short link is redirected to the original address, and each link counts its clicks. Users can delete their own links, and an admin can disable any link.

Endpoints: POST /api/auth/register, POST /api/auth/login, POST /api/links, GET /api/links, GET /api/links/{id}, DELETE /api/links/{id}, GET /api/admin/links, POST /api/admin/links/{id}/toggle.

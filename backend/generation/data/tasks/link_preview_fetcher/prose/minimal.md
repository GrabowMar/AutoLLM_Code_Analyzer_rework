Build a link bookmarking app with previews. Signed-in users save web links; the server fetches each page and shows a preview card with its title, description and image; users list, refresh and delete their saved links.

Endpoints: POST /api/auth/register, POST /api/auth/login, POST /api/previews, GET /api/previews, GET /api/previews/{id}, POST /api/previews/{id}/refresh, DELETE /api/previews/{id}.

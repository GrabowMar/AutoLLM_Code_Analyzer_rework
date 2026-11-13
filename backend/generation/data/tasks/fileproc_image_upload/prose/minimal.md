Build a public image gallery. Signed-in users upload PNG and JPEG images (base64 in JSON) with an optional title; anyone browses the gallery and opens images full size; uploaders delete their own images.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/images, POST /api/images, GET /api/images/{id}, GET /api/images/{id}/file, DELETE /api/images/{id}.

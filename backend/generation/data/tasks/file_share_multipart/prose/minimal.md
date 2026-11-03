Build a file sharing app. Signed-in users upload files (multipart form) with an optional description, list, download and delete their own files, and create share links that let anyone download a file without an account; links can be revoked.

Endpoints: POST /api/auth/register, POST /api/auth/login, POST /api/files, GET /api/files, GET /api/files/{id}, GET /api/files/{id}/download, DELETE /api/files/{id}, POST /api/files/{id}/shares, DELETE /api/files/{id}/shares/{token}, GET /api/shared/{token}.

Build an account service: people register, sign in, see and edit their profile (display name, email) and change their password. An admin lists every account and can deactivate or reactivate it; deactivated accounts cannot sign in.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/me, PATCH /api/me, POST /api/me/password, GET /api/admin/users, POST /api/admin/users/{id}/toggle.

Build an account service with password reset. Users register with a username, password and email, sign in, and change their email or password; someone who forgot their password requests a reset, gets a link by email (stored in an outbox the app exposes) and sets a new password.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/me, PATCH /api/me, POST /api/me/password, POST /api/auth/password-reset, POST /api/auth/password-reset/confirm, GET /api/outbox.

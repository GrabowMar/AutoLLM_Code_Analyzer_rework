Build an account service that resists password guessing. Users register and sign in; after five failed sign-ins in a row a username is locked for 15 minutes, and too many attempts from one client are refused for a while. The admin can unlock a username.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/me, GET /api/me/login-attempts, GET /api/admin/locks, POST /api/admin/locks/{username}/unlock.

Build a small online bank. Users register, sign in and sign out; every account starts with 1000.00; users transfer money to other users by username, with an optional memo, and see their balance and history.

Endpoints: POST /api/auth/register, POST /api/auth/login, POST /api/auth/logout, GET /api/auth/csrf, GET /api/account, POST /api/transfers, GET /api/transfers, GET /api/transfers/{id}.

Build a server status board. Signed-in users register servers (name and host) and report check results (online or offline, response time); anyone sees each server's current status, uptime and check history. The app never contacts the hosts itself.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/servers, POST /api/servers, GET /api/servers/{id}, PUT /api/servers/{id}, DELETE /api/servers/{id}, GET /api/servers/{id}/checks, POST /api/servers/{id}/checks.

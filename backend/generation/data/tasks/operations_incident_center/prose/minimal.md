Build an incident center for an operations team. Signed-in members open incidents (title, service, severity), move them through investigating, mitigated, monitoring and resolved, and post timeline events; the app tracks resolution targets per severity. A public page shows open counts.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/incidents, POST /api/incidents, GET /api/incidents/{id}, PATCH /api/incidents/{id}, GET /api/incidents/{id}/timeline, POST /api/incidents/{id}/timeline, GET /api/public/stats.

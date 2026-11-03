Build a sensor dashboard. Signed-in users register sensors (name, unit, location) and post numeric readings to their own sensors; anyone sees every sensor's latest value and a chart of its recent readings, refreshed automatically.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/sensors, POST /api/sensors, GET /api/sensors/{id}, DELETE /api/sensors/{id}, GET /api/sensors/{id}/readings, POST /api/sensors/{id}/readings.

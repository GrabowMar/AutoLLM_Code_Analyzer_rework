Build a subscription billing console for a team. Signed-in members manage customer subscriptions (plan, monthly amount, renewal date, status), issue invoices and record failed or successful payment collection runs; a public page shows MRR and counts per status.

Endpoints: POST /api/auth/register, POST /api/auth/login, POST /api/subscriptions, GET /api/subscriptions, GET /api/subscriptions/{id}, PATCH /api/subscriptions/{id}, GET /api/subscriptions/{id}/invoices, POST /api/subscriptions/{id}/invoices, POST /api/subscriptions/{id}/collections, GET /api/public/stats.

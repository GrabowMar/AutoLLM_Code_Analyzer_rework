Build a warehouse inventory app. Anyone can browse items and their stock history; signed-in users add, edit and delete items and record stock movements (received or taken out). Items at or below their reorder level are flagged as low stock.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/items, POST /api/items, GET /api/items/{id}, PUT /api/items/{id}, DELETE /api/items/{id}, GET /api/items/{id}/movements, POST /api/items/{id}/movements.

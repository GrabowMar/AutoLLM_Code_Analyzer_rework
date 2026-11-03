Build a small shop. Anyone can browse and search products; signed-in users add products to a personal cart, change quantities and see line totals and a subtotal. The admin adds products and edits their price and stock.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/products, GET /api/products/{id}, GET /api/cart, POST /api/cart, PATCH /api/cart/{id}, DELETE /api/cart/{id}, POST /api/admin/products, PATCH /api/admin/products/{id}.

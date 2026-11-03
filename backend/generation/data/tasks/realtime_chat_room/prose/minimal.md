Build a single shared chat room. Anyone reads the message history and receives new messages live over a WebSocket; signed-in users send messages and typing notices. Messages survive a restart. The admin can hide a message.

Endpoints: POST /api/auth/register, POST /api/auth/login, POST /api/messages, GET /api/messages, GET /api/ws/chat, POST /api/admin/messages/{id}/toggle.

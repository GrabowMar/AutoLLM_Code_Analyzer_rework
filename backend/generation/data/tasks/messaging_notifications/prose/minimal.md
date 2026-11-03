Build a notification center. Each signed-in user has a private inbox: they add notifications, filter by read state and type, mark them read or unread (or all read), and delete them. The admin broadcasts announcements to every inbox.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/notifications, POST /api/notifications, POST /api/notifications/read-all, GET /api/notifications/{id}, PATCH /api/notifications/{id}, DELETE /api/notifications/{id}, POST /api/admin/broadcast.

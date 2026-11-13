Build a base64 converter. Anyone encodes UTF-8 text to base64 and decodes base64 back to text; signed-in users can save conversions to a private history and delete entries from it.

Endpoints: POST /api/auth/register, POST /api/auth/login, POST /api/base64/encode, POST /api/base64/decode, POST /api/conversions, GET /api/conversions, DELETE /api/conversions/{id}.

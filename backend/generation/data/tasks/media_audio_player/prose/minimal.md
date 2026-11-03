Build a shared audio library with a browser player. Signed-in users add tracks by link (title, artist, audio address, duration); anyone searches the library, plays tracks and sees play counts; users delete their own tracks.

Endpoints: POST /api/auth/register, POST /api/auth/login, GET /api/tracks, POST /api/tracks, GET /api/tracks/{id}, DELETE /api/tracks/{id}, POST /api/tracks/{id}/plays.

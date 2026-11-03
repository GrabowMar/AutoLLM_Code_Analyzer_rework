Build an audio library with a JSON API under `/api` and a web frontend. Tracks are stored as links to audio files: the app keeps each track's address and metadata and never stores or fetches audio itself. Signed-in users add tracks; anyone can play them.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/tracks` lists tracks, newest first, as a paged list. `q` matches title or artist case-insensitively as a substring; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/tracks` adds a track for the signed-in user. On success it answers 201. It answers 400 for invalid input.
- `GET /api/tracks/{id}` returns one track. It answers 404 for an unknown id.
- `DELETE /api/tracks/{id}` deletes one of the user's tracks. On success it answers 204. It answers 403 for a track of another user; 404 for an unknown id.
- `POST /api/tracks/{id}/plays` adds one to the track's `play_count` and returns the track; the player calls it when a track starts. It answers 404 for an unknown id.

Data:

- A track is returned as an object with `id`, `title`, `artist`, `url`, `duration`, `play_count`, `created_at`, `added_by`.
- `GET /api/tracks` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a track.

Validation (invalid input answers 400 and changes nothing):

- Track: `title` required, at most 200 characters; `artist` optional, at most 200 characters; `url` required, an absolute `http` or `https` URL, at most 500 characters; `duration` optional, a whole number of seconds from 0 to 86400.

Rules:

- Only `http` and `https` audio addresses are accepted.
- Only the user who added a track may delete it.

Edge cases:

- Play counts never decrease.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- The track list with title, artist, duration and play count, a search box and paging.
- A player bar with play/pause, seek, volume, the current track's title and artist, and next/previous through the list.
- For signed-in users: an add-track form showing the API's validation messages, and delete with confirmation on their own tracks.

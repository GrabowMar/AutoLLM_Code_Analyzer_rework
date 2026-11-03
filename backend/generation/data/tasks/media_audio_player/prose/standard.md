Build an audio library with a JSON API under `/api` and a web frontend. Tracks are stored as links to audio files: the app keeps each track's address and metadata and never stores or fetches audio itself. Signed-in users add tracks; anyone can play them.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/tracks` lists tracks, newest first, as a paged list (query parameters: `q`, `page`, `page_size`).
- `POST /api/tracks` adds a track for the signed-in user.
- `GET /api/tracks/{id}` returns one track.
- `DELETE /api/tracks/{id}` deletes one of the user's tracks.
- `POST /api/tracks/{id}/plays` adds one to the track's `play_count` and returns the track; the player calls it when a track starts.

Data:

- A track is returned as an object with `id`, `title`, `artist`, `url`, `duration`, `play_count`, `created_at`, `added_by`.
- `GET /api/tracks` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a track.

Rules:

- Only `http` and `https` audio addresses are accepted.
- Only the user who added a track may delete it.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- The track list with title, artist, duration and play count, a search box and paging.
- A player bar with play/pause, seek, volume, the current track's title and artist, and next/previous through the list.
- For signed-in users: an add-track form showing the API's validation messages, and delete with confirmation on their own tracks.

Build a sensor dashboard with a JSON API under `/api` and a web frontend. Signed-in users register sensors and post readings to their own sensors; anyone can watch the latest values and the recent history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/sensors` lists sensors sorted by name, each with `owner`, `last_value` and `last_updated`.
- `POST /api/sensors` registers a sensor owned by the signed-in user. On success it answers 201. It answers 400 for invalid input.
- `GET /api/sensors/{id}` returns one sensor. It answers 404 for an unknown id.
- `DELETE /api/sensors/{id}` deletes one of the user's sensors and its readings. On success it answers 204. It answers 403 for a sensor of another user; 404 for an unknown id.
- `GET /api/sensors/{id}/readings` lists a sensor's readings, newest first, with `total`, the number of all its readings. `limit` caps the readings returned (default 50, at most 500). It answers 404 for an unknown id.
- `POST /api/sensors/{id}/readings` records a reading for one of the user's sensors and makes it the sensor's latest value. On success it answers 201. It answers 400 for a missing or non-numeric value; 403 for a sensor of another user; 404 for an unknown id.

Data:

- A sensor is returned as an object with `id`, `name`, `unit`, `location`, `last_value`, `last_updated`, `owner`.
- A reading is returned as an object with `id`, `sensor_id`, `value`, `recorded_at`.
- `GET /api/sensors` returns `{"items": [...]}`; each item in `items` is a sensor.
- `GET /api/sensors/{id}/readings` returns `{"items": [...], "total": n}`; each item in `items` is a reading.

Validation (invalid input answers 400 and changes nothing):

- Sensor: `name` required, at most 100 characters; `unit` required, at most 20 characters; `location` optional, at most 100 characters.
- Reading: `value` required, a number.

Rules:

- Only the user who registered a sensor may post readings to it or delete it.
- A new sensor has `last_value` and `last_updated` `null` until its first reading.
- `recorded_at` is set by the server.

Edge cases:

- Readings are never edited.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A card per sensor with its latest value, unit, location and how long ago it was updated; the page refreshes every 10 seconds.
- A sensor page with a line chart of its recent readings and the readings as a table.
- For signed-in users: a form to register a sensor, a form to post a reading to their own sensors and delete with confirmation.

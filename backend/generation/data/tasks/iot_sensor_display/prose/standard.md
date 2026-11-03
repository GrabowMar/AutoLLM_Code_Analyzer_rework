Build a sensor dashboard with a JSON API under `/api` and a web frontend. Signed-in users register sensors and post readings to their own sensors; anyone can watch the latest values and the recent history.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/sensors` lists sensors sorted by name, each with `owner`, `last_value` and `last_updated`.
- `POST /api/sensors` registers a sensor owned by the signed-in user.
- `GET /api/sensors/{id}` returns one sensor.
- `DELETE /api/sensors/{id}` deletes one of the user's sensors and its readings.
- `GET /api/sensors/{id}/readings` lists a sensor's readings, newest first, with `total`, the number of all its readings (query parameters: `limit`).
- `POST /api/sensors/{id}/readings` records a reading for one of the user's sensors and makes it the sensor's latest value.

Data:

- A sensor is returned as an object with `id`, `name`, `unit`, `location`, `last_value`, `last_updated`, `owner`.
- A reading is returned as an object with `id`, `sensor_id`, `value`, `recorded_at`.
- `GET /api/sensors` returns `{"items": [...]}`; each item in `items` is a sensor.
- `GET /api/sensors/{id}/readings` returns `{"items": [...], "total": n}`; each item in `items` is a reading.

Rules:

- Only the user who registered a sensor may post readings to it or delete it.
- A new sensor has `last_value` and `last_updated` `null` until its first reading.
- `recorded_at` is set by the server.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A card per sensor with its latest value, unit, location and how long ago it was updated; the page refreshes every 10 seconds.
- A sensor page with a line chart of its recent readings and the readings as a table.
- For signed-in users: a form to register a sensor, a form to post a reading to their own sensors and delete with confirmation.

Build a weather display with a JSON API under `/api` and a web frontend. The app serves current conditions from its own store of weather reports and calls no outside service. Anyone can look up a city; signed-in users maintain the reports. Every lookup is recorded.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/weather` lists reports ordered by city name as a paged list; with `city` it keeps only the report of that city and records the lookup. `city` matches the city name exactly, ignoring case and surrounding spaces, and records a lookup of the name as typed (an empty list when nothing matches); `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/weather` adds a report for a city. On success it answers 201. It answers 400 for invalid input; 409 when the city already has a report.
- `GET /api/weather/{id}` returns one report. It answers 404 for an unknown id.
- `PUT /api/weather/{id}` replaces a report's fields; omitted optional fields are cleared. It answers 400 for invalid input; 404 for an unknown id; 409 when another report has that city.
- `DELETE /api/weather/{id}` deletes a report. On success it answers 204. It answers 404 for an unknown id.
- `GET /api/weather/recent` returns the most recent lookups, newest first. `limit` caps the number of lookups (default 10, at most 50).

Data:

- A weather report is returned as an object with `id`, `city`, `country`, `temperature_c`, `condition`, `humidity`, `wind_kph`, `observed_at`.
- A lookup is returned as an object with `id`, `city`, `found`, `looked_up_at`.
- `GET /api/weather` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a weather report.
- `GET /api/weather/recent` returns `{"items": [...]}`; each item in `items` is a lookup.

Validation (invalid input answers 400 and changes nothing):

- Weather report: `city` required, at most 100 characters, unique ignoring case; `country` optional, at most 100 characters; `temperature_c` required, a number from -90 to 60; `condition` required, one of `sunny`, `cloudy`, `rain`, `snow`, `storm`, `fog`; `humidity` optional, a whole number from 0 to 100; `wind_kph` optional, a number from 0 to 500.

Rules:

- City names are unique ignoring case.
- `observed_at` is set by the server whenever a report is created or replaced; temperatures are in degrees Celsius.
- On first start the store holds sample reports for at least five cities.

Edge cases:

- A lookup records the name as typed and whether a report was found.
- Deleting a report keeps the lookups that found it.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A city search that shows a weather card (city, country, temperature, condition, humidity, wind, observed time) or a 'No weather data for <city>' message.
- A Celsius/Fahrenheit switch that converts the shown temperatures in the browser.
- The five most recent lookups; clicking one repeats the search.
- For signed-in users: the list of reports with add and edit forms showing the API's validation messages, and delete after confirmation.

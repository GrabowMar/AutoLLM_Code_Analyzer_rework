Build a weather display with a JSON API under `/api` and a web frontend. The app serves current conditions from its own store of weather reports and calls no outside service. Anyone can look up a city; signed-in users maintain the reports. Every lookup is recorded.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/weather` lists reports ordered by city name as a paged list; with `city` it keeps only the report of that city and records the lookup (query parameters: `city`, `page`, `page_size`).
- `POST /api/weather` adds a report for a city.
- `GET /api/weather/{id}` returns one report.
- `PUT /api/weather/{id}` replaces a report's fields; omitted optional fields are cleared.
- `DELETE /api/weather/{id}` deletes a report.
- `GET /api/weather/recent` returns the most recent lookups, newest first (query parameters: `limit`).

Data:

- A weather report is returned as an object with `id`, `city`, `country`, `temperature_c`, `condition`, `humidity`, `wind_kph`, `observed_at`.
- A lookup is returned as an object with `id`, `city`, `found`, `looked_up_at`.
- `GET /api/weather` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a weather report.
- `GET /api/weather/recent` returns `{"items": [...]}`; each item in `items` is a lookup.

Rules:

- City names are unique ignoring case.
- `observed_at` is set by the server whenever a report is created or replaced; temperatures are in degrees Celsius.
- On first start the store holds sample reports for at least five cities.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A city search that shows a weather card (city, country, temperature, condition, humidity, wind, observed time) or a 'No weather data for <city>' message.
- A Celsius/Fahrenheit switch that converts the shown temperatures in the browser.
- The five most recent lookups; clicking one repeats the search.
- For signed-in users: the list of reports with add and edit forms showing the API's validation messages, and delete after confirmation.

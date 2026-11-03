Build a marketing campaign monitor with a JSON API under `/api` and a web frontend. Anyone can browse campaigns and their performance; signed-in users create campaigns, record one performance snapshot per day and move campaigns through their lifecycle. The app computes spend, conversions and budget pacing from the snapshots.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/campaigns` lists campaigns as a paged list, newest first by default. `q` matches the name case-insensitively as a substring; `status` keeps one status; `channel` keeps one channel; `sort` is one of `-created_at` (default), `name`, `launch_at`, `-spend_to_date`; `page` is the 1-based page number (default 1); `page_size` is the number of items per page (default 20, at most 100).
- `POST /api/campaigns` creates a campaign owned by the signed-in user, in status `draft`. On success it answers 201. It answers 400 for invalid input.
- `GET /api/campaigns/{id}` returns one campaign with its computed fields. It answers 404 for an unknown id.
- `PATCH /api/campaigns/{id}` changes some of a campaign's fields or its status; only the owner may do this. Allowed status changes: `draft` to `active`, `active` to `paused`, `paused` to `active`, and `active` or `paused` to `completed`; setting the current status again is allowed. It answers 400 for invalid input; 403 for a user who does not own the campaign; 404 for an unknown id; 409 for a status change that is not allowed or any change to a completed campaign.
- `GET /api/campaigns/{id}/performance` returns the campaign's snapshots oldest first, their totals and the rates `ctr` (clicks / impressions), `conversion_rate` (conversions / clicks) and `cost_per_conversion` (spend / conversions). Each rate is rounded to four decimals and is `null` when its divisor is 0. It answers 404 for an unknown id.
- `POST /api/campaigns/{id}/performance` records one day's figures for a campaign; only the owner may do this, and posting a date that already has a snapshot replaces it. A new date answers 201, a replaced one 200. On success it answers 201. It answers 400 for invalid input; 403 for a user who does not own the campaign; 404 for an unknown id.

Data:

- A campaign is returned as an object with `id`, `name`, `channel`, `status`, `daily_budget`, `launch_at`, `end_at`, `notes`, `created_at`, `owner`, `spend_to_date`, `conversions`, `pacing`, `pacing_status`.
- A performance snapshot is returned as an object with `id`, `campaign_id`, `date`, `impressions`, `clicks`, `conversions`, `spend`, `revenue`.
- `GET /api/campaigns` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a campaign.
- `GET /api/campaigns/{id}/performance` returns `{"items": [...], "totals": {...}, "ctr": ..., "conversion_rate": ..., "cost_per_conversion": ...}`; each item in `items` is a performance snapshot.

Validation (invalid input answers 400 and changes nothing):

- Campaign: `name` required, at most 120 characters; `channel` required, one of `search`, `social`, `email`, `video`; `status` starts as `draft`; changed only through the allowed transitions; `daily_budget` required, a number from 0.01 to 1000000; `launch_at` required, an ISO 8601 date-time; `end_at` optional, an ISO 8601 date-time not before `launch_at`; `notes` optional, at most 2000 characters.
- Performance snapshot: `date` required, a date `YYYY-MM-DD`; one snapshot per campaign and date; `impressions` required, a whole number of at least 0; `clicks` required, a whole number of at least 0 and at most `impressions`; `conversions` required, a whole number of at least 0 and at most `clicks`; `spend` required, a number of at least 0; `revenue` required, a number of at least 0.

Rules:

- Only a campaign's owner may change it or record its performance; everyone may read it.
- `spend_to_date` and `conversions` are sums over the campaign's snapshots; `pacing` is `spend_to_date` divided by (`daily_budget` times the number of snapshot days), rounded to two decimals, and `null` without snapshots.
- `pacing_status` is `over` above 1.10, `under` below 0.90, `on_track` otherwise, and `no_data` without snapshots.

Edge cases:

- In a snapshot, `clicks` may not exceed `impressions` and `conversions` may not exceed `clicks` (400).
- A completed campaign accepts no further changes (409) but still accepts no new snapshots either (409).

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A campaign table with name, channel, status, owner, daily budget, spend to date and a pacing badge, with a name search, status and channel filters, sort controls and paging.
- A campaign page with key figures, a chart of daily spend and conversions and the table of snapshots.
- For the owner: create and edit forms, status buttons offering only the allowed changes, and a form to record a day's figures, each showing the API's validation messages.

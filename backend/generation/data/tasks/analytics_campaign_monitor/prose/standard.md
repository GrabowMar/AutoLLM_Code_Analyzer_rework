Build a marketing campaign monitor with a JSON API under `/api` and a web frontend. Anyone can browse campaigns and their performance; signed-in users create campaigns, record one performance snapshot per day and move campaigns through their lifecycle. The app computes spend, conversions and budget pacing from the snapshots.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `GET /api/campaigns` lists campaigns as a paged list, newest first by default (query parameters: `q` (name search), `status`, `channel`, `sort`, `page`, `page_size`).
- `POST /api/campaigns` creates a campaign owned by the signed-in user, in status `draft`.
- `GET /api/campaigns/{id}` returns one campaign with its computed fields.
- `PATCH /api/campaigns/{id}` changes some of a campaign's fields or its status; only the owner may do this.
- `GET /api/campaigns/{id}/performance` returns the campaign's snapshots oldest first, their totals and the rates `ctr` (clicks / impressions), `conversion_rate` (conversions / clicks) and `cost_per_conversion` (spend / conversions).
- `POST /api/campaigns/{id}/performance` records one day's figures for a campaign; only the owner may do this, and posting a date that already has a snapshot replaces it.

Data:

- A campaign is returned as an object with `id`, `name`, `channel`, `status`, `daily_budget`, `launch_at`, `end_at`, `notes`, `created_at`, `owner`, `spend_to_date`, `conversions`, `pacing`, `pacing_status`.
- A performance snapshot is returned as an object with `id`, `campaign_id`, `date`, `impressions`, `clicks`, `conversions`, `spend`, `revenue`.
- `GET /api/campaigns` returns `{"items": [...], "total": n, "page": n, "page_size": n}`; each item in `items` is a campaign.
- `GET /api/campaigns/{id}/performance` returns `{"items": [...], "totals": {...}, "ctr": ..., "conversion_rate": ..., "cost_per_conversion": ...}`; each item in `items` is a performance snapshot.

Rules:

- Only a campaign's owner may change it or record its performance; everyone may read it.
- `spend_to_date` and `conversions` are sums over the campaign's snapshots; `pacing` is `spend_to_date` divided by (`daily_budget` times the number of snapshot days), rounded to two decimals, and `null` without snapshots.
- `pacing_status` is `over` above 1.10, `under` below 0.90, `on_track` otherwise, and `no_data` without snapshots.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- A campaign table with name, channel, status, owner, daily budget, spend to date and a pacing badge, with a name search, status and channel filters, sort controls and paging.
- A campaign page with key figures, a chart of daily spend and conversions and the table of snapshots.
- For the owner: create and edit forms, status buttons offering only the allowed changes, and a form to record a day's figures, each showing the API's validation messages.

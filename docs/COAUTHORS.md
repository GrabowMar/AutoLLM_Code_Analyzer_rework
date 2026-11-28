# Co-authors

A user can invite other people into their workspace with a link. Whoever joins becomes a co-author and can see and do everything the inviting user can: studies, runs, applications, analyses, reports, models, endpoints, keys, budgets and human-evaluation tasks.

The code is the `backend/coauthors` app. The API is `/api/coauthors/`.

## How it works

Nothing is shared object by object. A co-author **works as** the owner:

1. The owner creates an invite link in Settings → Co-authors.
2. The invited person opens it, signs in (or signs up) and presses **Join workspace**. This creates a `Coauthorship` row (owner, member).
3. The member's session stores which workspace is selected. While one is selected, every API request is checked against the database and then handled as the owner: `request.user`, `request.auser()` and ninja's `request.auth` are the owner. The real person is `request.actor`.
4. Objects the member creates belong to the owner (`created_by`), so credentials, budgets and background work resolve as usual.
5. The owner can remove a co-author at any time. The check runs on every request, so removal takes effect on the member's next request; an open event stream ends within 30 seconds.

Users without a co-authorship are not affected: no new fields in existing responses and no extra queries on `/api/` requests.

Helpers for code that needs the real person: `backend.coauthors.identity.real_user(request)` and `is_acting(request)`.

## What a co-author cannot do

Sign-in and persistence surfaces always belong to the real person.

| Surface | Rule |
|---|---|
| `/_allauth/`, `/accounts/` (login, e-mail, password, MFA, sessions) | Never swapped. A co-author changes their own account there, never the owner's |
| Django admin, `/users/` | Never swapped |
| `/api/tokens/` (personal API tokens) | Denied while a workspace is selected |
| `PATCH /api/users/me/`, `PATCH /api/users/{id}/` | Denied while a workspace is selected |
| Invite links and the co-author list (`/api/coauthors/invites/`, `/api/coauthors/members/`) | Own account only. A co-author cannot invite further people |
| Bearer tokens, MCP, relay | A token always means the user it belongs to. A member cannot work in a workspace through a token |
| Human-evaluation portal | Never swapped: a member's ratings are their own |
| Sample apps at `/apps/…` | Never swapped (see [Sample apps](#sample-apps-on-the-platform-origin)) |
| Address of an endpoint that has a stored key | Changing it needs the key entered again in the same request, so a stored key cannot be sent to another server |
| Address of a webhook that has a stored secret (ntfy token, signing secret) | Changing it needs the secret entered again (or cleared) in the same request, for the same reason |

Co-authorships are not transitive. A co-author of A does not get the workspaces A is a co-author of.

A co-author of an administrator has the administrator's access in the API (other people's work, system settings), but not Django admin. The invite dialog says so.

## The policy table

`backend/coauthors/policy.py` decides what happens to a request while a workspace is selected.

In scope: paths under `/api/` without an `Authorization` header, except `/api/mcp…`, `/api/relay/v1/`, `/api/runtime/app-tls-check/` and `/api/human-eval/portal/`. Everything else is left alone.

| Mode | Meaning |
|---|---|
| `SWAP` | The request runs as the workspace owner |
| `REAL` | The request stays the real person |
| `DENY` | 403 `acting_denied` |

Everything in scope is `SWAP`, except under the personal prefixes, which are deny-by-default:

| Prefix | Exceptions |
|---|---|
| `/api/tokens/` | None |
| `/api/users/` | `SWAP`: `GET /api/users/`, `GET /api/users/me/`, `GET /api/users/me/activity/`, `GET /api/users/<id>/`. `REAL`: `/api/users/bootstrap-status/`, `/api/users/bootstrap-admin/` |
| `/api/coauthors/` | `REAL`: `context/`, `workspace/`, `join/…`, `memberships/…` |

A selection that is no longer valid (removed, left, owner deactivated) is dropped and answered with 409 `workspace_ended`. Requests that were already in flight together can each get it; after that, requests run as the member.

### Several tabs

The selected workspace belongs to the session, so it is shared by every tab of the browser. A tab that changes it (switch, join, leave) tells the other tabs, which reload under the new identity. As a second check, the web app sends `X-Workspace: <coauthorship id | own>` (the workspace the tab shows) on every changing request. If that differs from the session's choice, the middleware answers 409 `workspace_changed` before the request runs, and the tab reloads. Routes in `REAL` mode are not checked, and requests without the header are not affected.

The second line of defence is at the routers: `PersonalSessionAuth` (personal tokens, co-authors) refuses a swapped request, and `OwnAccountAuth` (invites, members) refuses while any workspace is selected. Both are session-only.

### Adding a router

A new router under `/api/` gets parity automatically. If it manages a sign-in (credentials of the account itself, sessions, people with access), add its prefix to `PERSONAL_PREFIXES` in `policy.py` and give it `PersonalSessionAuth`.

`test_s12_route_snapshot` in `backend/coauthors/tests/test_security.py` lists every top-level API segment. It fails when a router is added, until the new segment is classified there.

An endpoint that returns a stored secret value must not be reachable in `SWAP` mode. None exists today.

A stored secret must not be sent to an address a co-author can change, unless the secret is entered again in the same request. Today that applies to `LLMEndpoint.base_url` and `WebhookSubscription.url`. `test_s08_every_stored_secret_is_classified` lists every `encrypted_*` field and every route handler that reads a stored secret; it fails when a new one appears, until it is classified.

## API

All routes are session-only with CSRF. Errors are `{"detail", "code", "errors": []}`.

| Route | Who | What |
|---|---|---|
| `GET /api/coauthors/context/` | Signed in | The real person, the selected workspace and the workspaces they can switch to |
| `POST /api/coauthors/workspace/` | Signed in | Select a workspace (`{"coauthorship_id": 12}`) or one's own account (`null`) |
| `POST /api/coauthors/join/preview/` | Anyone | What a link offers. Changes nothing. Token in the body |
| `POST /api/coauthors/join/accept/` | Signed in | Join and select the workspace. Token in the body |
| `DELETE /api/coauthors/memberships/{id}/` | Member | Leave |
| `GET`, `POST /api/coauthors/invites/` | Owner | List links, create one (label, 1–30 days, 1–20 uses). The token is returned once |
| `DELETE /api/coauthors/invites/{id}/` | Owner | Revoke a link |
| `GET /api/coauthors/members/` | Owner | Co-authors, active first |
| `DELETE /api/coauthors/members/{id}/` | Owner | Remove a co-author. The link they joined with is revoked, and no other link made before the removal lets them back in |
| `GET /api/coauthors/members/{id}/activity/` | Owner | What that co-author changed (`limit`, `before`) |

`GET /api/users/me/` returns the owner while a workspace is selected. Use `context/` for the real person.

## Invite links

- The link is `<origin>/join#<token>`. The token is in the URL fragment, so it never reaches a server log or a `Referer` header. API calls send it in a POST body only.
- Only the sha256 of the token is stored. The link is shown once, when it is created.
- Nothing happens on GET. Joining needs a signed-in POST after an explicit confirm screen that shows the inviter's e-mail address.
- Every unusable link (unknown, revoked, expired, used up, owner deactivated) gets the same 404.
- Failed lookups are throttled: per account for signed-in callers (20 in 10 minutes), per client address for signed-out callers.
- The owner gets an e-mail for every new join.
- An owner can have 20 active links. A link lasts 1 to 30 days (default 7) and admits 1 to 20 people (default 1).

### Sign-up when registration is closed

With `DJANGO_ACCOUNT_ALLOW_REGISTRATION=False`, an invite link lets its holder create an account only if **an administrator created the link**, and for at most `max_uses` accounts per link. Links from other users still work for people who already have an account.

The preview stores a flag in the visitor's own session; `AccountAdapter.is_open_for_signup` checks it and locks the invite row for the sign-up. Registering does not join: the new user still confirms on the join page after logging in.

## Activity trail

There are no actor columns on existing models. What a co-author does is stored as the owner's work. The real actor is kept in two places:

- `Coauthorship.last_active_at`, updated at most once a minute.
- `CoauthorAction`: one row (method, path, status code) for every request other than GET, HEAD and OPTIONS that a co-author makes in a workspace, including denied ones (403).

No query string, body or header is stored. Rows are written only by the middleware; no API changes them and Django admin is read-only for them. The join routes are not recorded.

## Sample apps on the platform origin

A sample app opened at `/apps/<name>/` runs on the platform origin with the viewer's session, so whoever shaped the app can act as the viewer.

- `/apps/` is never swapped. A member cannot open the owner's apps there; they use the app's own address.
- Where apps have their own origin (`APPS_SUBDOMAIN_PROXY` or `TRAEFIK_DYNAMIC_DIR`), `/apps/` answers 403 for anyone who has ever had a co-author. The UI links to the app's own address on those deployments.
- **Deployments with path mode only are exposed.** Without app subdomains the block is not applied, because the owner would have no way to open their own apps. A co-author can then run script in the owner's session through an app the owner opens. Set up app subdomains before inviting anyone on such a deployment.

## Accepted limits

- A co-author of an administrator is an administrator in everything but Django admin and sign-in.
- Things a co-author creates outlive their removal: queued runs, schedules, relay tokens, webhooks, replaced keys, evaluator accounts. None is a way back into the API. The trail shows who made them.
- The owner changing their password does not remove co-authors. Removal is the control; the join e-mail is the alarm.
- A co-author without MFA weakens an owner with MFA for the workspace data.
- A flood of bad tokens can throttle the signed-out preview on deployments without `TRUSTED_PROXY_CIDRS`. Signed-in users are not affected.
- The trail is deleted with the account of either side. Only an administrator can delete accounts.
- Whoever runs the server can add a co-authorship row by hand. They cannot recover an invite link.

## Tests

`backend/coauthors/tests/`. `test_security.py` holds one test per attack, numbered S01 to S34:

| Tests | Area |
|---|---|
| S01–S15 | Getting more than the owner's data: sign-in, tokens, user routes, management, chains, admin, staff parity, stored keys, sample apps, evaluators, new routers, bearer tokens, the portal |
| S16–S21 | The invite token: URLs and logs, side effects, dead links, throttling, inviter identity, storage |
| S22–S27 | Accepting: CSRF, forged selections, races, own link, removal keeps the person out, the join e-mail |
| S28–S29 | Closed registration |
| S30–S32 | Removal, open streams, deleted and deactivated accounts, tabs behind a workspace switch |
| S33–S34 | The activity trail |

Run them with an isolated database:

```bash
POSTGRES_DB=llmlab_coauthors pytest backend/coauthors
```

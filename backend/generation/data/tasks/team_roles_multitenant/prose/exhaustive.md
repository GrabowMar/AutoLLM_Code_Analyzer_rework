Build a multi-tenant project tracker with a JSON API under `/api` and a web frontend. Users create organizations and invite other users with a role; each organization's projects are visible only to its members, and what a member may do depends on their role.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`. `username` is required, unique and at most 50 characters; `password` is required. On success it answers 201. It answers 400 for invalid input; 409 when the username is taken.
- `POST /api/auth/login` signs in with the `username` and `password`. It answers 401 for a wrong username or password, without saying which of the two was wrong. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/orgs` creates an organization; its creator becomes its owner. On success it answers 201. It answers 400 for invalid input.
- `GET /api/orgs` lists the organizations the user belongs to, each with the user's `role`.
- `GET /api/orgs/{org_id}/members` lists an organization's members with their roles; any member may do this. It answers 404 for an unknown organization or one the user is not a member of.
- `POST /api/orgs/{org_id}/members` adds an existing user with a role; owners and admins may do this, and only an owner may add an owner. On success it answers 201. It answers 400 for invalid input; 403 for a member whose role does not allow it; 404 for an unknown organization or one the user is not a member of, or an unknown username; 409 when the user is already a member.
- `PATCH /api/orgs/{org_id}/members/{username}` changes a member's role; only owners may do this. It answers 400 for invalid input; 403 for a member who is not an owner; 404 for an unknown organization or one the user is not a member of, or a username that is not a member; 409 when it would leave the organization without an owner.
- `DELETE /api/orgs/{org_id}/members/{username}` removes a member; owners may remove anyone, admins may remove members and viewers, and anyone may remove themselves. On success it answers 204. It answers 403 for a member whose role does not allow it; 404 for an unknown organization or one the user is not a member of, or a username that is not a member; 409 when it would remove the last owner.
- `GET /api/orgs/{org_id}/projects` lists an organization's projects, newest first; any member may do this. `status` keeps active or archived projects. It answers 404 for an unknown organization or one the user is not a member of.
- `POST /api/orgs/{org_id}/projects` creates a project in the organization; owners, admins and members may do this, viewers may not. On success it answers 201. It answers 400 for invalid input; 403 for a viewer; 404 for an unknown organization or one the user is not a member of.
- `GET /api/projects/{id}` returns one project to any member of its organization. It answers 404 for an unknown project or one of an organization the user is not a member of.
- `PATCH /api/projects/{id}` changes a project's `name`, `description` or `status`; viewers may not. It answers 400 for invalid input; 403 for a viewer; 404 for an unknown project or one of an organization the user is not a member of.
- `DELETE /api/projects/{id}` deletes a project; only owners and admins may do this. On success it answers 204. It answers 403 for a member or viewer; 404 for an unknown project or one of an organization the user is not a member of.

Data:

- An organization is returned as an object with `id`, `name`, `created_at`, `role`.
- A membership is returned as an object with `id`, `username`, `role`.
- A project is returned as an object with `id`, `name`, `description`, `status`, `org_id`, `created_at`.
- `GET /api/orgs` returns `{"items": [...]}`; each item in `items` is an organization.
- `GET /api/orgs/{org_id}/members` returns `{"items": [...]}`; each item in `items` is a membership.
- `GET /api/orgs/{org_id}/projects` returns `{"items": [...]}`; each item in `items` is a project.

Validation (invalid input answers 400 and changes nothing):

- Organization: `name` required, at most 100 characters.
- Membership: `username` required, an existing account's username; `role` required, one of `owner`, `admin`, `member`, `viewer`.
- Project: `name` required, at most 120 characters; `description` optional, at most 2000 characters; `status` optional, one of `active`, `archived`, default `active`.

Rules:

- An organization and everything in it are visible only to its members; to anyone else they behave as if they did not exist (404).
- Roles: owners may do everything; admins manage members (except owners) and projects; members create and edit projects; viewers only read.
- An organization always keeps at least one owner.

Edge cases:

- A project id from another organization answers 404, even for an owner of a different organization.
- Changing one's own role is allowed for owners only, and never removes the last owner.

Every error response is a JSON object `{"detail": "<message>"}`.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- An organization switcher listing the user's organizations with their role, and a 'New organization' form.
- A members page with roles, an 'Add member' form and role and remove controls shown only when the user's role allows them.
- The organization's projects with a status filter, create and edit forms and delete with confirmation, each control shown only when the role allows it.

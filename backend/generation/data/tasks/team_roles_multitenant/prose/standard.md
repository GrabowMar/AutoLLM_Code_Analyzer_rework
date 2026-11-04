Build a multi-tenant project tracker with a JSON API under `/api` and a web frontend. Users create organizations and invite other users with a role; each organization's projects are visible only to its members, and what a member may do depends on their role.

API:

- `POST /api/auth/register` creates an account from a `username` and a `password`; `POST /api/auth/login` signs in with the `username` and `password`. How a signed-in user is recognised on later requests is described in the account section.
- `POST /api/orgs` creates an organization; its creator becomes its owner.
- `GET /api/orgs` lists the organizations the user belongs to, each with the user's `role`.
- `GET /api/orgs/{org_id}/members` lists an organization's members with their roles; any member may do this.
- `POST /api/orgs/{org_id}/members` adds an existing user with a role; owners and admins may do this, and only an owner may add an owner.
- `PATCH /api/orgs/{org_id}/members/{username}` changes a member's role; only owners may do this.
- `DELETE /api/orgs/{org_id}/members/{username}` removes a member; owners may remove anyone, admins may remove members and viewers, and anyone may remove themselves.
- `GET /api/orgs/{org_id}/projects` lists an organization's projects, newest first; any member may do this (query parameters: `status`).
- `POST /api/orgs/{org_id}/projects` creates a project in the organization; owners, admins and members may do this, viewers may not.
- `GET /api/projects/{id}` returns one project to any member of its organization.
- `PATCH /api/projects/{id}` changes a project's `name`, `description` or `status`; viewers may not.
- `DELETE /api/projects/{id}` deletes a project; only owners and admins may do this.

Data:

- An organization is returned as an object with `id`, `name`, `created_at`, `role`.
- A membership is returned as an object with `id`, `username`, `role`.
- A project is returned as an object with `id`, `name`, `description`, `status`, `org_id`, `created_at`.
- `GET /api/orgs` returns `{"items": [...]}`; each item in `items` is an organization.
- `GET /api/orgs/{org_id}/members` returns `{"items": [...]}`; each item in `items` is a membership.
- `GET /api/orgs/{org_id}/projects` returns `{"items": [...]}`; each item in `items` is a project.

Rules:

- An organization and everything in it are visible only to its members; to anyone else they behave as if they did not exist (404).
- Roles: owners may do everything; admins manage members (except owners) and projects; members create and edit projects; viewers only read.
- An organization always keeps at least one owner.

Web frontend:

- Registration and sign-in forms; the signed-in username and a sign-out button.
- An organization switcher listing the user's organizations with their role, and a 'New organization' form.
- A members page with roles, an 'Add member' form and role and remove controls shown only when the user's role allows them.
- The organization's projects with a status filter, create and edit forms and delete with confirmation, each control shown only when the role allows it.

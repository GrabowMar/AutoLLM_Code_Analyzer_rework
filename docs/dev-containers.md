# Dev Containers (VS Code)

Prerequisites: Docker Desktop running and the
[Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
extension.

1. Open the repo folder in VS Code.
2. Run **Dev Containers: Reopen in Container** from the Command Palette.

On first open, [`initializeCommand`](../.devcontainer/devcontainer.json) runs
`scripts/bootstrap.py` on your machine to create `.envs/.local/.django` and
`.postgres` from the committed templates (same as `just bootstrap`). You can
also run bootstrap manually before reopening:

```bash
just bootstrap
# or
python scripts/bootstrap.py
```

On Windows, if `python` is not on PATH, try `py -3 scripts/bootstrap.py`.

The dev container attaches to the `django` Compose service as user
`dev-user`, with the workspace at `/app`. The full local Compose stack
starts (same services as `just up`): Postgres, Redis, Mailpit, Django,
Celery, Flower, and frontend.

After the container is ready:

```bash
python manage.py createsuperuser   # optional — migrate runs via /start on django
```

Browse at <http://localhost:8000> (frontend) and <http://localhost:8001>
(Django). Django starts in the background after attach
(`/tmp/django-start.log` in the container).

## Troubleshooting

- **`django-1` exits with "password authentication failed"** — reset the
  Postgres volume: `docker compose -f docker-compose.local.yml down -v`,
  then `just up`.
- **Celery or frontend missing in Docker Desktop** — from the host run
  `docker compose -f docker-compose.local.yml up -d`.
- **Reopen fails with `env file ... .django not found`** — run
  `python scripts/bootstrap.py`, verify
  `docker compose -f docker-compose.local.yml config` exits 0, and reopen.
- **`Could not connect to WSL` / read-only `/root` from Docker Desktop** —
  usually harmless if Docker Engine is running.
- **Attach dies with exit code 137 during "Installing VS Code Server"** —
  give Docker Desktop ≥ 6–8 GB RAM (Settings → Resources), run
  `docker compose -f docker-compose.local.yml down`, then
  **Rebuild and Reopen in Container**.

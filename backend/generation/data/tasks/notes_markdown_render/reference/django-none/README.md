# App

## Run contract

- The app is built from `Dockerfile` in this directory and the container listens on port 8000.
- The API is served under `/api`. `GET /api/health` answers `{"status": "ok"}` once the app is ready.
- The user interface is served by the backend, starting at `/`.
- Persistent data lives in `./data` (relative to the working directory `/app`).

## Layout

- `backend/`: the Django project (`backend/manage.py`, settings and URLs in `backend/config/`, served by gunicorn as `config.wsgi:application` after `migrate`); dependencies in `backend/requirements.txt`.

Dependencies are pinned: `backend/requirements.lock` holds the resolved versions and is installed as a pip constraints file. When you change dependencies, update the lock file as well (or remove it).

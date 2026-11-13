# App

## Run contract

- The app is built from `Dockerfile` in this directory and the container listens on port 8000.
- The API is served under `/api`. `GET /api/health` answers `{"status": "ok"}` once the app is ready.
- The user interface is served by the backend, starting at `/`.
- Persistent data lives in `./data` (relative to the working directory `/app`).

## Layout

- `backend/`: the Express app in TypeScript (`backend/src/server.ts`, run directly by Node.js 24); dependencies in `backend/package.json`.

Dependencies are pinned: `backend/package-lock.json` holds the resolved versions (`npm ci`). When you change dependencies, update the lock file as well (or remove it).

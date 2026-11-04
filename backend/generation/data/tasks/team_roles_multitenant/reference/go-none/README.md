# App

## Run contract

- The app is built from `Dockerfile` in this directory and the container listens on port 8000.
- The API is served under `/api`. `GET /api/health` answers `{"status": "ok"}` once the app is ready.
- The user interface is served by the backend, starting at `/`.
- Persistent data lives in `./data` (relative to the working directory `/app`).

## Layout

- `backend/`: the Go module (package `main`, standard library `net/http`; `backend/main.go` is the entry file, the image runs the built binary from `/app`); dependencies in `backend/go.mod`.

Dependencies are pinned: `backend/go.sum` holds the checksums of the modules `backend/go.mod` requires (`go mod tidy` updates both). When you change dependencies, update the lock file as well (or remove it).

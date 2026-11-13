# App

## Run contract

- The app is built from `Dockerfile` in this directory and the container listens on port 8000.
- The API is served under `/api`. `GET /api/health` answers `{"status": "ok"}` once the app is ready.
- The user interface is served by the backend, starting at `/`.
- Persistent data lives in `./data` (relative to the working directory `/app`).

## Layout

- `backend/`: the Spring Boot app (Java 21, Maven; `backend/pom.xml`, sources in `backend/src/main/java/app/`, settings in `backend/src/main/resources/application.properties`). The image runs the executable jar that `mvn package` builds.

Dependencies are pinned: `backend/pom.xml` pins the Spring Boot parent, which manages every dependency version; `backend/dependency-list.txt` records the resolved runtime dependencies (`mvn dependency:list`). When you change dependencies, update the lock file as well (or remove it).

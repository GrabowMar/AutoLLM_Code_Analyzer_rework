# Todo list

Flask API under `/api` and a React frontend. Run with `docker build -t todo . && docker run -p 8000:8000 todo`.

Run contract: the container listens on port 8000, serves the API under `/api` (health check at
`/api/health`) and the frontend build from `/`. Data lives in `./data`.

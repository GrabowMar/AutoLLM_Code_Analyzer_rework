"""FastAPI entry point: the health check and the frontend build."""

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

app = FastAPI()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/{path:path}")
def spa(path: str = "") -> FileResponse:
    target = (DIST / path).resolve()
    if path and target.is_relative_to(DIST) and target.is_file():
        return FileResponse(target)
    return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))

"""LLM replaces this file with the generated FastAPI application."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Generated App")
_STATIC = Path(__file__).parent / "static"
if _STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=_STATIC / "assets"), name="assets")


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/{full_path:path}")
def spa(full_path: str = ""):
    index = _STATIC / "index.html"
    if index.is_file():
        return FileResponse(index)
    return {"detail": "frontend not built"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)

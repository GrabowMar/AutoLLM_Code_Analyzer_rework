"""LLM replaces this file with the generated FastAPI application.

Layout the Docker image expects: the app object is ``app`` in ``app.py``
(started with ``uvicorn app:app``), every API route lives under ``/api/``,
and the built SPA is in ``./static`` (Vite ``base: '/static/'``).
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Generated App")
_STATIC = Path(__file__).parent / "static"


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Built assets are requested as /static/assets/... (Vite base '/static/').
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")


# SPA fallback, registered last: any other path returns index.html so
# client-side routes and deep links load. Unknown /api/ paths stay 404.
@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path == "api" or full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not Found")
    index = _STATIC / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="frontend not built")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)

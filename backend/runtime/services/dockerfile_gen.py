"""Server-side Dockerfile generation for user-authored stacks.

User stacks never ship a Dockerfile: it is rendered here from a pinned shape
modeled on the builtin stacks (non-root user, EXPOSE from the stack's port,
healthcheck, pinned install commands only). Base images are validated against
``settings.STACK_ALLOWED_BASE_IMAGES`` before a stack row is saved, so the
values interpolated here come from the allowlist, not from free user input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.runtime.models import Stack

_FRONTEND_STAGE = """\
# Stage 1: build the frontend (server-generated — user stacks cannot ship Dockerfiles)
FROM {frontend_image} AS frontend-builder
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install --silent
COPY frontend/ ./
RUN npm run build

"""

_BACKEND_STAGE = """\
FROM {backend_image}
WORKDIR /app

RUN apt-get update \\
    && apt-get install -y --no-install-recommends curl \\
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
{copy_frontend}RUN mkdir -p data

RUN addgroup --system appgroup \\
    && adduser --system --ingroup appgroup appuser \\
    && chown -R appuser:appgroup /app
USER appuser

EXPOSE {port}

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \\
    CMD curl -f http://localhost:{port}/api/health || exit 1

CMD {command}
"""

_COPY_FRONTEND = "COPY --from=frontend-builder /frontend/dist ./static\n"


def generate_dockerfile(stack: Stack) -> str:
    """Render the Dockerfile for a ``generated``-mode stack row."""
    port = stack.default_port
    module = stack.backend_filename.removesuffix(".py")
    if stack.server_kind == "uvicorn":
        command = f'["uvicorn", "{module}:app", "--host", "0.0.0.0", "--port", "{port}"]'
    else:
        command = f'["python", "{stack.backend_filename}"]'

    parts = []
    if stack.has_frontend:
        parts.append(_FRONTEND_STAGE.format(frontend_image=stack.frontend_base_image))
    parts.append(
        _BACKEND_STAGE.format(
            backend_image=stack.backend_base_image,
            copy_frontend=_COPY_FRONTEND if stack.has_frontend else "",
            port=port,
            command=command,
        ),
    )
    return "".join(parts)

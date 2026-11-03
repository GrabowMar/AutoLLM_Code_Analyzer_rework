# Shared sandbox environment for every agent harness (spec section 5.3, "common rules").
# Harness images build FROM this exact image, so the tools an agent finds (Python, Node, git,
# ripgrep, ...) are byte-identical across harnesses; only the harness layer differs.
# The base release is pinned exactly: a floating tag would change the environment under an
# unchanged content hash. (Debian packages still resolve at build time; the built image digest is
# recorded per run in the manifest.)
# Runtimes match the builtin stacks' Dockerfiles: Node 24 here, and trixie's Debian python3 is 3.13.
FROM node:24.22.1-trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git ripgrep python3 python3-venv python3-pip curl procps bzip2 \
    && rm -rf /var/lib/apt/lists/*

# v3 layout (harnesses/base.py): /workspace (the app), /logs (gateway + harness evidence),
# /run (brief, rendered harness files) and /run/home, the fresh HOME every harness gets, so no
# user config, cached login or project context file is ever found outside the rendered files.
RUN mkdir -p /workspace /logs /run/home /opt/harness \
    && chown node:node /workspace /logs /run/home

# Debian marks its Python as externally managed; inside a throwaway sandbox plain
# `pip install` (to ~/.local) should just work, as on a dev machine.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/run/home/.local/bin:$PATH \
    npm_config_update_notifier=false \
    npm_config_fund=false \
    HOME=/run/home

USER node
WORKDIR /workspace

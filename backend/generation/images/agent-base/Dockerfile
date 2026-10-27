# Shared sandbox environment for every agent harness. Harness images build
# FROM this exact image, so the tools an agent finds (Python, Node, git, ...)
# are byte-identical across harnesses; only the harness layer differs.
# The base release is pinned exactly: a floating tag would change the
# environment under an unchanged content hash. (Debian packages still resolve
# at build time; the built image id is recorded per run.)
# Runtimes match the builtin stacks' Dockerfiles: Node 24 here, and trixie's
# Debian python3 is 3.13 (the stacks run python:3.13-slim).
FROM node:24.22.1-trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates git ripgrep python3 python3-venv python3-pip curl procps \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /workspace /out /opt/harness && chown node:node /workspace /out

# Debian marks its Python as externally managed; inside a throwaway sandbox
# plain `pip install` (to ~/.local) should just work, as on a dev machine.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/home/node/.local/bin:$PATH \
    npm_config_update_notifier=false \
    npm_config_fund=false

USER node
WORKDIR /workspace

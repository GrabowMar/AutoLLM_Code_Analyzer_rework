# Base image for per-user analyzer workspaces.
#
# Tools from the catalog ("shop") are installed into a *running* container of
# this image on demand (pip / npm) and then executed against user code copied
# into /work. The image ships only the language runtimes and package managers
# the tools need — the tools themselves are installed at "install" time so the
# shop model is exercised end to end.
FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NPM_CONFIG_PREFIX=/home/analyzer/.npm-global \
    PATH="/home/analyzer/.local/bin:/home/analyzer/.npm-global/bin:${PATH}"

# Node.js (for eslint and other JS tooling) + coreutils `timeout`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg coreutils \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Non-root user; /work is the writable mount point for code under analysis.
RUN useradd --create-home --shell /bin/bash analyzer \
    && mkdir -p /work /home/analyzer/.npm-global \
    && chown -R analyzer:analyzer /work /home/analyzer

USER analyzer
WORKDIR /work

CMD ["sleep", "infinity"]

# Codex CLI harness layer on the shared sandbox base (see agent-base/; spec section 5.3).
# BASE_IMAGE is the content-addressed agent-base tag, passed by agentic/images.py.
# @openai/codex is a Node launcher for a prebuilt Rust binary, installed by npm from the
# platform-specific optionalDependency (npm alias @openai/codex@<version>-linux-x64).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG CODEX_VERSION=0.166.0
RUN npm install -g "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && HOME=/tmp/codex-home codex --version \
    && rm -rf /tmp/codex-home
USER node

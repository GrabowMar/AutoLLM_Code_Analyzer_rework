# Claude Code harness layer on the shared sandbox base (see agent-base/; spec section 5.3).
# BASE_IMAGE is the content-addressed agent-base tag, passed by agentic/images.py.
# The npm package is a thin launcher; npm installs the matching prebuilt platform binary from its
# optionalDependencies (@anthropic-ai/claude-code-linux-x64 on glibc) at build time. Build-time network
# is allowed; nothing is fetched at run time (auto-update and nonessential traffic are off).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG CLAUDE_CODE_VERSION=2.1.316
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    && npm cache clean --force \
    && HOME=/tmp/claude-home claude --version \
    && rm -rf /tmp/claude-home
USER node

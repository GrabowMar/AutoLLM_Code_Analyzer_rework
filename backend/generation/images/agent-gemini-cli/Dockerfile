# Gemini CLI harness layer on the shared sandbox base (see agent-base/; spec section 5.3).
# BASE_IMAGE is the content-addressed agent-base tag, passed by agentic/images.py.
# Disabled until wave 3 (needs the WP-06 gemini_generate translator and the loopback forwarder that
# harnesses/gemini_cli.py renders into /run/harness).
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG GEMINI_CLI_VERSION=0.66.0
RUN npm install -g "@google/gemini-cli@${GEMINI_CLI_VERSION}" \
    && npm cache clean --force \
    && HOME=/tmp/gemini-home gemini --version \
    && rm -rf /tmp/gemini-home
USER node

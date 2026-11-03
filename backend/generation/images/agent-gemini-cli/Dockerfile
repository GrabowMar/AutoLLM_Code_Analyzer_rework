# Gemini CLI harness layer on the shared sandbox base (see agent-base/; spec section 5.3).
# BASE_IMAGE is the content-addressed agent-base tag, passed by agentic/images.py.
# The per-run files (user settings, pinned prompt, the loopback forwarder) are rendered by
# harnesses/gemini_cli.py into /run; only the run-independent switches live here, as the system settings
# file: the CLI reads it only from a root-owned directory, which rendered files never are.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG GEMINI_CLI_VERSION=0.66.0
RUN npm install -g "@google/gemini-cli@${GEMINI_CLI_VERSION}" \
    && npm cache clean --force \
    && HOME=/tmp/gemini-home gemini --version \
    && rm -rf /tmp/gemini-home
COPY --chown=root:root system-settings.json /etc/gemini-cli/settings.json
RUN chmod 0755 /etc/gemini-cli && chmod 0644 /etc/gemini-cli/settings.json
USER node

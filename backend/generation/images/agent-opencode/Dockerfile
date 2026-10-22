# OpenCode harness layer on the shared sandbox base (see agent-base/).
# BASE_IMAGE is the content-addressed agent-base tag, passed by sandbox.py.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG OPENCODE_VERSION=1.18.35
RUN npm install -g "opencode-ai@${OPENCODE_VERSION}" && npm cache clean --force
USER node

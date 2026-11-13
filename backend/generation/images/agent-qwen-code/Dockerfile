# Qwen Code harness layer on the shared sandbox base (see agent-base/; spec section 5.3).
# BASE_IMAGE is the content-addressed agent-base tag, passed by agentic/images.py. Needs Node >= 22.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
ARG QWEN_CODE_VERSION=0.26.6
RUN npm install -g "@qwen-code/qwen-code@${QWEN_CODE_VERSION}" \
    && npm cache clean --force \
    && HOME=/tmp/qwen-home qwen --version \
    && rm -rf /tmp/qwen-home
USER node

# Aider harness layer on the shared sandbox base (see agent-base/).
# Installed into a private venv so its packages never show up in the agent's
# own `python3`/`pip`.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}

USER root
# Every transitive dependency is pinned: an unpinned install resolved a
# litellm release that would not even import. Regenerate with
# `pip install aider-chat==<v> && pip freeze` inside agent-base.
COPY requirements.lock /opt/harness/requirements.lock
RUN python3 -m venv /opt/harness/venv \
    && /opt/harness/venv/bin/pip install --no-cache-dir --no-deps -r /opt/harness/requirements.lock
USER node

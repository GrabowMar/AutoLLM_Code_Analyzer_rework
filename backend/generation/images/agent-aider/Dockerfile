# Aider harness layer on the shared sandbox base (see agent-base/).
# Installed into a private venv so its packages never show up in the agent's
# own `python3`/`pip`.
ARG BASE_IMAGE
# aider-chat (every release up to 0.86.2) requires Python <3.13, while the
# base's Debian python3 is 3.13. The venv therefore runs on its own pinned
# CPython 3.12, copied from the official image (same Debian release as the
# base; that build is linked with an $ORIGIN-relative rpath, so it runs from
# any prefix). The agent's own `python3` is unaffected.
FROM python:3.12.14-slim-trixie AS harness-python

FROM ${BASE_IMAGE}

USER root
COPY --from=harness-python /usr/local /opt/harness/python
# Every transitive dependency is pinned: an unpinned install resolved a
# litellm release that would not even import. Regenerate with
# `pip install aider-chat==<v> && pip freeze` in a venv made by
# /opt/harness/python/bin/python3.12 inside this image.
COPY requirements.lock /opt/harness/requirements.lock
RUN /opt/harness/python/bin/python3.12 -m venv /opt/harness/venv \
    && /opt/harness/venv/bin/pip install --no-cache-dir --no-deps -r /opt/harness/requirements.lock
USER node

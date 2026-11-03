# Read-only PyPI pull-through cache (devpi-server) for agent sandboxes (spec 4.2).
FROM python:3.13-slim@sha256:ae14d4ce8771606477a81446e7ef429edd6762305fc50c84507c1041fa8285d1
RUN pip install --no-cache-dir --disable-pip-version-check devpi-server==6.21.0 \
    && useradd --system --create-home --home-dir /devpi devpi
COPY entrypoint.sh /usr/local/bin/devpi-entrypoint
USER devpi
VOLUME /devpi/server
EXPOSE 3141
ENTRYPOINT ["sh", "/usr/local/bin/devpi-entrypoint"]

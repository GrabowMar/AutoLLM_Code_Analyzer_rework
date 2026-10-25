# define an alias for the specific python version used in this file.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS python

# Python build stage
FROM python AS python-build-stage

ARG APP_HOME=/app

WORKDIR ${APP_HOME}

# we need to move the virtualenv outside of the $APP_HOME directory because it will be overriden by the docker compose mount
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

# Install apt packages
RUN apt-get update && apt-get install --no-install-recommends -y \
  # dependencies for building Python packages
  build-essential \
  # psycopg dependencies
  libpq-dev \
  gettext \
  wait-for-it

# Requirements are installed here to ensure they will be cached.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock:rw \
    uv sync --no-install-project

COPY . ${APP_HOME}

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock:rw \
    uv sync

# devcontainer dependencies and utils
RUN apt-get update && apt-get install --no-install-recommends -y \
  sudo git bash-completion nano ssh

# Create devcontainer user and add it to sudoers
RUN groupadd --gid 1000 dev-user \
  && useradd --uid 1000 --gid dev-user --shell /bin/bash --create-home dev-user \
  && echo dev-user ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/dev-user \
  && chmod 0440 /etc/sudoers.d/dev-user

# docker CLI for the devcontainer; DOCKER_HOST points it at the apps-dind
# sandbox (see .devcontainer/docker-compose.devcontainer.yml).
COPY --from=docker.io/library/docker:29.9.1-cli /usr/local/bin/docker /usr/local/bin/docker

# Empty, dev-user-owned mount points for the devcontainer's named volumes
# (see .devcontainer/docker-compose.devcontainer.yml). Docker seeds a new named
# volume with the image directory's ownership, so dev-user can write to them.
RUN mkdir -p /home/dev-user/.venv /home/dev-user/.cache ${APP_HOME}/frontend/node_modules \
  && chown dev-user:dev-user /home/dev-user/.venv /home/dev-user/.cache ${APP_HOME}/frontend/node_modules

ENV PATH="/${APP_HOME}/.venv/bin:$PATH"
ENV PYTHONPATH="${APP_HOME}/.venv/lib/python3.13/site-packages:$PYTHONPATH"

COPY ./compose/production/django/entrypoint /entrypoint
RUN sed -i 's/\r$//g' /entrypoint
RUN chmod +x /entrypoint

COPY ./compose/local/django/start /start
RUN sed -i 's/\r$//g' /start
RUN chmod +x /start

COPY ./compose/local/django/start-executor /start-executor
RUN sed -i 's/\r$//g' /start-executor
RUN chmod +x /start-executor


COPY ./compose/local/django/celery/worker/start /start-celeryworker
RUN sed -i 's/\r$//g' /start-celeryworker
RUN chmod +x /start-celeryworker

COPY ./compose/local/django/celery/beat/start /start-celerybeat
RUN sed -i 's/\r$//g' /start-celerybeat
RUN chmod +x /start-celerybeat

COPY ./compose/local/django/celery/flower/start /start-flower
RUN sed -i 's/\r$//g' /start-flower
RUN chmod +x /start-flower


ENTRYPOINT ["/entrypoint"]

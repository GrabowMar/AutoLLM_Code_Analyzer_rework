# Sandbox Docker daemon for generated sample apps and analyzer workspaces.
# Django/Celery talk to it over mutual TLS (tcp://apps-dind:2376); every app it
# builds and runs is a nested container, never a container on the host daemon.
FROM docker:29.9.1-dind

# Log rotation + a build-cache budget, so nested containers can't fill the disk.
COPY ./compose/local/apps-dind/daemon.json /etc/docker/daemon.json
COPY ./compose/local/apps-dind/entrypoint.sh /usr/local/bin/apps-dind-entrypoint
RUN chmod +x /usr/local/bin/apps-dind-entrypoint

ENTRYPOINT ["apps-dind-entrypoint"]
CMD []

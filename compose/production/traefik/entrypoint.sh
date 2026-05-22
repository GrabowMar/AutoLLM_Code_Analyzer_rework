#!/bin/sh
# Render traefik.yml and dynamic/static.yml from templates, then exec traefik.
set -eu

: "${DJANGO_DOMAIN:?DJANGO_DOMAIN env var is required}"
: "${LETSENCRYPT_EMAIL:=admin@${DJANGO_DOMAIN}}"
export DJANGO_DOMAIN LETSENCRYPT_EMAIL

envsubst '${DJANGO_DOMAIN} ${LETSENCRYPT_EMAIL}' \
    < /etc/traefik/traefik.yml.template \
    > /etc/traefik/traefik.yml

envsubst '${DJANGO_DOMAIN}' \
    < /etc/traefik/static.yml.template \
    > /etc/traefik/dynamic/static.yml

exec /entrypoint.sh "$@"

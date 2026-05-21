#!/bin/sh
# Render the traefik config from the template using the current env, then
# exec the upstream traefik entrypoint.
set -eu

: "${DJANGO_DOMAIN:?DJANGO_DOMAIN env var is required}"
: "${LETSENCRYPT_EMAIL:=admin@${DJANGO_DOMAIN}}"
export DJANGO_DOMAIN LETSENCRYPT_EMAIL

envsubst '${DJANGO_DOMAIN} ${LETSENCRYPT_EMAIL}' \
    < /etc/traefik/traefik.yml.template \
    > /etc/traefik/traefik.yml

exec /entrypoint.sh "$@"

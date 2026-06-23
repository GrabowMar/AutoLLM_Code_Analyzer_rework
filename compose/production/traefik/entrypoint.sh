#!/bin/sh
# Render traefik.yml and dynamic/static.yml from templates, then exec traefik.
set -eu

: "${DJANGO_DOMAIN:?DJANGO_DOMAIN env var is required}"
: "${LETSENCRYPT_EMAIL:=admin@${DJANGO_DOMAIN}}"
: "${APPS_DOMAIN:=${DJANGO_DOMAIN}}"
: "${TRAEFIK_DNS_PROVIDER:?TRAEFIK_DNS_PROVIDER is required for the wildcard *.${APPS_DOMAIN} cert}"
export DJANGO_DOMAIN LETSENCRYPT_EMAIL APPS_DOMAIN TRAEFIK_DNS_PROVIDER

envsubst '${DJANGO_DOMAIN} ${LETSENCRYPT_EMAIL} ${TRAEFIK_DNS_PROVIDER}' \
    < /etc/traefik/traefik.yml.template \
    > /etc/traefik/traefik.yml

envsubst '${DJANGO_DOMAIN} ${APPS_DOMAIN}' \
    < /etc/traefik/static.yml.template \
    > /etc/traefik/dynamic/static.yml

exec /entrypoint.sh "$@"

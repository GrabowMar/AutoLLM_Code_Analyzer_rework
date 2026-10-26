#!/bin/sh
# Render traefik.yml and dynamic/static.yml from templates, then exec traefik.
set -eu

: "${DJANGO_DOMAIN:?DJANGO_DOMAIN env var is required}"
: "${LETSENCRYPT_EMAIL:=admin@${DJANGO_DOMAIN}}"
: "${APPS_DOMAIN:=${DJANGO_DOMAIN}}"
: "${TRAEFIK_DNS_PROVIDER:?TRAEFIK_DNS_PROVIDER is required for the wildcard *.${APPS_DOMAIN} cert}"
# Route the Django admin at whatever DJANGO_ADMIN_URL is (from .django), the
# same normalisation as config/settings/production.py: strip slashes, default
# "admin", always one leading and one trailing slash.
_admin="${DJANGO_ADMIN_URL:-}"
while [ "${_admin#/}" != "$_admin" ]; do _admin="${_admin#/}"; done
while [ "${_admin%/}" != "$_admin" ]; do _admin="${_admin%/}"; done
DJANGO_ADMIN_PATH="/${_admin:-admin}/"
export DJANGO_DOMAIN LETSENCRYPT_EMAIL APPS_DOMAIN TRAEFIK_DNS_PROVIDER DJANGO_ADMIN_PATH

envsubst '${DJANGO_DOMAIN} ${LETSENCRYPT_EMAIL} ${TRAEFIK_DNS_PROVIDER}' \
    < /etc/traefik/traefik.yml.template \
    > /etc/traefik/traefik.yml

envsubst '${DJANGO_DOMAIN} ${APPS_DOMAIN} ${DJANGO_ADMIN_PATH}' \
    < /etc/traefik/static.yml.template \
    > /etc/traefik/dynamic/static.yml

exec /entrypoint.sh "$@"

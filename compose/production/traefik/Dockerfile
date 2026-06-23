FROM docker.io/traefik:v3.7.1

# envsubst (from gettext) renders traefik.yml and dynamic/static.yml from templates at start.
RUN apk add --no-cache gettext \
    && mkdir -p /etc/traefik/acme /etc/traefik/dynamic \
    && touch /etc/traefik/acme/acme.json /etc/traefik/acme/acme-dns.json \
    && chmod 600 /etc/traefik/acme/acme.json /etc/traefik/acme/acme-dns.json

COPY ./compose/production/traefik/traefik.yml.template /etc/traefik/traefik.yml.template
COPY ./compose/production/traefik/dynamic/static.yml.template /etc/traefik/static.yml.template
COPY ./compose/production/traefik/entrypoint.sh /usr/local/bin/traefik-entrypoint.sh
RUN chmod +x /usr/local/bin/traefik-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/traefik-entrypoint.sh"]
CMD ["traefik"]

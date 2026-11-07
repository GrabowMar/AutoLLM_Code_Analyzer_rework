# Sending application email with Postal

Postal handles signup confirmations, password resets and invitations through
Django's existing SMTP backend. It runs as a separate Compose project, with its
own MariaDB volume, SMTP service, web UI and delivery worker. It does not use the
application PostgreSQL database. Postal is pinned to 3.3.7 and MariaDB to 11.4.

The setup command keeps existing email delivery working. It generates records
for a dedicated sending subdomain and only enables real delivery after domain
ownership, SPF, DKIM, return-path DNS, the public hostname and reverse DNS pass.

## Set up

Requirements: Docker Compose, Python 3, openssl, an existing domain and a server
with a public IPv4 address and outgoing TCP port 25. Allow incoming TCP port 25
for delivery failures. Postal's SMTP submission network and database are private;
the admin UI binds only to `127.0.0.1:8026`.

```bash
just postal-setup --hostname postal.example.com \
  --sender-domain notify.example.com --public-ip YOUR_PUBLIC_IPV4
just postal-dns
```

The script creates credentials and signing keys in gitignored `var/postal/`.
Its parent directory is mode 700 and `credentials.json` is mode 600. Read the
admin login from that file locally. Do not commit or paste its contents.
Setup is safe to rerun for the same hostname/domain/IP; it preserves credentials,
keys, the sending domain and the existing server mode.

Publish the exact records in `var/postal/dns-records.md`. Keep these records
DNS-only if your provider offers an HTTP proxy. For TXT records longer than 255
bytes, use multiple quoted chunks inside one record. Set the server's PTR to the
Postal hostname using your hosting provider's reverse-DNS settings. Existing
mailboxes and MX records on the parent domain need no changes.

## Enable email delivery

```bash
just postal-verify
just postal-enable
just postal-test you@example.com
```

`postal-enable` sets the Postal server to Live only after DNS passes, backs up
the Django environment to `.django.before-postal`, writes the authenticated SMTP
settings and recreates Django and Celery worker. Confirmation is mandatory.
Authentication uses a generated credential over a private Docker network;
external clients must not submit passwords to the public bounce listener.

The script stores `COMPOSE_FILE` in the app's `.env` so future `docker compose`
and `just up` calls retain `docker-compose.postal.yml`. A shell-level
`COMPOSE_FILE` takes precedence; include the Postal overlay if you set it.
Other app processes pick up the same settings on their next planned recreation.

For production or a deployment with existing overrides, pass all Compose files
in their normal order. `--app-root` can target a different checkout:

```bash
python3 scripts/postal.py configure-app --app-root /path/to/app \
  --environment local \
  --compose-file /path/to/app/docker-compose.local.yml \
  --compose-file /path/to/app/.devcontainer/docker-compose.host.yml
```

The target checkout needs the SMTP settings changes, `docker-compose.postal.yml`
and the updated `justfile`. Production uses `--environment production` and
`docker-compose.production.yml`. The Postal overlay is appended automatically.

## Check delivery and maintain

`just postal-status` shows service status. The admin UI shows queued messages,
delivery attempts, bounces and recipient SMTP responses. SMTP acceptance means
the message was queued; confirm final delivery in Postal and the recipient inbox.
Use an SSH tunnel to access the UI remotely:

```bash
ssh -L 8026:127.0.0.1:8026 user@your-server
```

Then open `http://127.0.0.1:8026`. If you publish the UI later, put it behind an
HTTPS reverse proxy and update `postal.web_hostname`/`postal.web_protocol` in the
generated config. Restart Postal services after config changes.

Back up the `llmlab-postal_database` volume and `var/postal/` together. They
contain the configuration, credentials and private DKIM keys. Postal keeps raw
message bodies for 3 days and message metadata for 14 days. Docker logs rotate.
Review upstream release notes before changing the image version and run
`postal upgrade` against the Postal database before starting the new services.

Official references: [installation](https://docs.postalserver.io/getting-started/installation/),
[DNS configuration](https://docs.postalserver.io/getting-started/dns-configuration/),
[SMTP authentication](https://docs.postalserver.io/features/smtp-authentication/).

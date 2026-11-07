set dotenv-load
export COMPOSE_FILE := env_var_or_default("COMPOSE_FILE", "docker-compose.local.yml")

## Just does not yet manage signals for subprocesses reliably, which can lead to unexpected behavior.
## Exercise caution before expanding its usage in production environments.
## For more information, see https://github.com/casey/just/issues/2473 .


# Default command to list all available commands.
default:
    @just --list

# bootstrap: Create local .env files from templates and generate secrets.
bootstrap:
    @python scripts/bootstrap.py

# build: Build python image.
build *args:
    @echo "Building python image..."
    @docker compose build {{args}}

# up: Start up containers.
up:
    @echo "Starting up containers..."
    @docker compose up -d --remove-orphans

# down: Stop containers.
down:
    @echo "Stopping containers..."
    @docker compose down

# prune: Remove containers and their volumes.
prune *args:
    @echo "Killing containers and removing volumes..."
    @docker compose down -v {{args}}

# logs: View container logs
logs *args:
    @docker compose logs -f {{args}}

# test: Run the backend test suite (same command as CI).
test *args:
    @docker compose run --rm django pytest {{args}}

# manage: Executes `manage.py` command.
manage +args:
    @docker compose run --rm django python ./manage.py {{args}}

# frontend-install: Install frontend dependencies.
frontend-install:
    @docker compose run --rm frontend npm install

# frontend-dev: Start frontend dev server.
frontend-dev:
    @docker compose up frontend

# frontend-build: Build frontend for production.
frontend-build:
    @docker compose run --rm frontend npm run build

# postal-setup: Provision Postal and generate the sending-domain DNS records.
postal-setup +args:
    @python3 scripts/postal.py setup {{args}}

# postal-dns: Display the exact DNS records for this Postal installation.
postal-dns:
    @python3 scripts/postal.py dns

# postal-status: Show Postal's persistent services.
postal-status:
    @python3 scripts/postal.py status

# postal-verify: Check sending-domain authentication and server reverse DNS.
postal-verify:
    @python3 scripts/postal.py verify

# postal-enable: Verify DNS and switch the app to Postal SMTP.
postal-enable *args:
    @python3 scripts/postal.py configure-app {{args}}

# postal-test: Send a test email through Django and Postal to a real recipient.
postal-test recipient:
    @python3 scripts/postal.py test-email {{recipient}}

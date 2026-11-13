#!/usr/bin/env python3
"""Provision Postal and connect Django after its sending DNS is verified.

Uses only Python's standard library, Docker Compose and openssl. Secrets and
generated configuration live in gitignored var/postal, never in command output.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "POSTAL_WORKFLOW="
HOSTNAME = re.compile(r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\Z")


def write_file(path: Path, content: str, mode: int = 0o600) -> None:
    """Atomically replace a file without exposing a partially written secret."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            temporary.chmod(mode)
            handle.write(content)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def update_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            msg = f"Invalid newline in {key}"
            raise ValueError(msg)
        # Quote values for Compose dotenv parsing; credentials are alphanumeric.
        line = f"{key}='{value.replace(chr(39), chr(92) + chr(39))}'"
        found = False
        updated = []
        for existing in lines:
            if re.match(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=", existing):
                if not found:
                    updated.append(line)
                    found = True
            else:
                updated.append(existing)
        if not found:
            updated.append(line)
        lines = updated
    write_file(path, "\n".join(lines) + "\n")


def compose(state: Path, *args: str, capture: bool = False) -> str:
    command = [
        "docker",
        "compose",
        "--project-name",
        "llmlab-postal",
        "--env-file",
        str(state / "runtime.env"),
        "-f",
        str(ROOT / "compose/postal/docker-compose.yml"),
        *args,
    ]
    environment = {**os.environ, "POSTAL_STATE_DIR": str(state)}
    result = subprocess.run(command, env=environment, check=True, text=True, capture_output=capture)  # noqa: S603
    return result.stdout if capture else ""


def run_workflow(state: Path, action: str) -> dict:
    output = compose(
        state,
        "run",
        "--rm",
        "--no-deps",
        "-e",
        f"POSTAL_WORKFLOW_ACTION={action}",
        "runner",
        "bundle",
        "exec",
        "rails",
        "runner",
        "/workflow/provision.rb",
        capture=True,
    )
    for line in reversed(output.splitlines()):
        if line.startswith(MARKER):
            return json.loads(line[len(MARKER) :])
    msg = "Postal did not return workflow status. Inspect the Postal service logs."
    raise ValueError(msg)


def postal_config(spec: dict, smtp_password: str = "") -> dict:
    hostname = spec["hostname"]
    database = {"host": "db", "username": "root", "password": spec["database_password"]}
    return {
        "version": 2,
        "postal": {"web_hostname": "127.0.0.1", "web_protocol": "http", "smtp_hostname": hostname},
        "web_server": {"default_bind_address": "0.0.0.0", "default_port": 5000},  # noqa: S104 - loopback published port
        "main_db": {**database, "database": "postal"},
        "message_db": {**database, "prefix": "postal"},
        "smtp_server": {"default_bind_address": "0.0.0.0", "default_port": 2525},  # noqa: S104 - container listener
        "dns": {
            "mx_records": [hostname],
            "spf_include": f"spf.{hostname}",
            "return_path_domain": f"rp.{hostname}",
            "route_domain": f"routes.{hostname}",
            "track_domain": f"track.{hostname}",
            "helo_hostname": hostname,
        },
        "smtp": {
            "host": "smtp",
            "port": 2525,
            "username": "llmlab",
            "password": smtp_password,
            "from_name": "Postal",
            "from_address": f"noreply@{spec['sender_domain']}",
        },
        "rails": {"secret_key": spec["rails_secret"]},
    }


def save_dns(state: Path, result: dict) -> None:
    public = {key: value for key, value in result.items() if key != "smtp_password"}
    write_file(state / "dns-records.json", json.dumps(public, indent=2) + "\n")
    rows = [
        "# Postal DNS records",
        "",
        "Publish these records with your DNS provider (DNS-only, without an HTTP proxy).",
        "",
        "| Type | Full record name | Value |",
        "| --- | --- | --- |",
    ]
    rows += [f"| {r['type']} | {r['name']} | `{r['value']}` |" for r in result["records"]]
    ptr_note = (
        f"Set the server's reverse DNS (PTR) for **{result['public_ip']}** to **{result['hostname']}** "
        "in your hosting provider's panel."
    )
    txt_note = (
        "TXT values longer than 255 bytes must be split into quoted chunks within a single DNS record; "
        "most DNS panels do this automatically."
    )
    rows += [
        "",
        ptr_note,
        "",
        txt_note,
        "",
        "After propagation, run `python3 scripts/postal.py verify`, then `python3 scripts/postal.py configure-app`.",
        "",
    ]
    write_file(state / "dns-records.md", "\n".join(rows))


def setup(state: Path, args: argparse.Namespace) -> None:
    for value in [args.hostname, args.sender_domain]:
        if not HOSTNAME.fullmatch(value):
            msg = f"Expected a fully qualified DNS hostname: {value}"
            raise ValueError(msg)
    if not ipaddress.IPv4Address(args.public_ip).is_global:
        msg = "--public-ip must be the server's public IPv4 address"
        raise ValueError(msg)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    state.chmod(0o700)
    config = state / "config"
    config.mkdir(mode=0o755, exist_ok=True)
    spec_path = config / "workflow.json"
    if spec_path.exists():
        spec = json.loads(spec_path.read_text())
        expected = (args.hostname, args.sender_domain, args.public_ip)
        if (spec["hostname"], spec["sender_domain"], spec["public_ip"]) != expected:
            msg = "This installation already uses a different hostname, domain or IP; existing secrets were preserved."
            raise ValueError(msg)
    else:
        spec = {
            "hostname": args.hostname,
            "sender_domain": args.sender_domain,
            "public_ip": args.public_ip,
            "admin_email": args.admin_email or f"admin@{args.sender_domain}",
            "admin_password": secrets.token_urlsafe(32),
            "database_password": secrets.token_hex(32),
            "rails_secret": secrets.token_hex(64),
        }
        # Only Postal containers can read this mount; the host parent is mode 700.
        write_file(spec_path, json.dumps(spec, indent=2) + "\n", 0o644)
    write_file(state / "runtime.env", f"MARIADB_ROOT_PASSWORD={spec['database_password']}\n")
    postal_path = config / "postal.yml"
    if not postal_path.exists():
        write_file(postal_path, json.dumps(postal_config(spec), indent=2) + "\n", 0o644)
    key_path = config / "signing.key"
    if not key_path.exists():
        subprocess.run(  # noqa: S603
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(key_path)],  # noqa: S607
            check=True,
            capture_output=True,
        )
        key_path.chmod(0o644)
    compose(state, "up", "-d", "--wait", "--wait-timeout", "90", "db")
    print("Initializing Postal's dedicated database...", flush=True)
    compose(state, "run", "--rm", "--no-deps", "runner", "postal", "initialize", capture=True)
    result = run_workflow(state, "provision")
    write_file(
        state / "credentials.json",
        json.dumps(
            {
                "admin_email": spec["admin_email"],
                "admin_password": spec["admin_password"],
                "smtp_username": "llmlab",
                "smtp_password": result["smtp_password"],
                "sender_domain": spec["sender_domain"],
            },
            indent=2,
        )
        + "\n",
    )
    write_file(postal_path, json.dumps(postal_config(spec, result["smtp_password"]), indent=2) + "\n", 0o644)
    save_dns(state, result)
    compose(state, "up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90", "web", "smtp", "worker")
    print(f"Postal is running. Admin UI: http://127.0.0.1:8026\nCredentials: {state / 'credentials.json'}")
    print(
        f"DNS records: {state / 'dns-records.md'}\n"
        "App delivery stays unchanged until DNS verification and configure-app succeed.",
    )


def verify(state: Path, action: str = "verify") -> dict:
    result = run_workflow(state, action)
    if result["ready_for_delivery"]:
        print("Postal domain ownership, SPF, DKIM, return path, hostname and reverse DNS are verified.")
    else:
        print("Postal is waiting for DNS:")
        for record in result["pending"]:
            print(f"  {record}")
        print(f"Ownership verified: {result['verified']}; SPF: {result['spf_status']}; DKIM: {result['dkim_status']}")
    return result


def app_compose_files(args: argparse.Namespace) -> list[Path]:
    root = args.app_root.resolve()
    files = (
        [Path(value).resolve() for value in args.compose_file]
        if args.compose_file
        else [
            root / f"docker-compose.{args.environment}.yml",
        ]
    )
    override = root / "docker-compose.postal.yml"
    if override not in files:
        files.append(override)
    if not all(path.is_file() for path in files):
        msg = "A Compose file is missing. Copy docker-compose.postal.yml to the target checkout first."
        raise ValueError(msg)
    return files


def configure_app(state: Path, args: argparse.Namespace) -> None:
    root = args.app_root.resolve()
    files = app_compose_files(args)
    if args.environment == "local" and 'env.int("EMAIL_PORT"' not in (root / "config/settings/local.py").read_text():
        msg = "The target checkout needs the environment-based SMTP settings in config/settings/local.py."
        raise ValueError(msg)
    if not verify(state, "activate")["ready_for_delivery"]:
        msg = "Publish the generated DNS/PTR records before switching registration emails to Postal."
        raise ValueError(msg)
    credentials = json.loads((state / "credentials.json").read_text())
    env_path = root / ".envs" / f".{args.environment}" / ".django"
    if not env_path.is_file():
        msg = f"Missing Django environment file: {env_path}"
        raise ValueError(msg)
    backup = env_path.with_name(".django.before-postal")
    if not backup.exists():
        write_file(backup, env_path.read_text())
    update_env(
        env_path,
        {
            "EMAIL_HOST": "postal-smtp",
            "EMAIL_PORT": "2525",
            "EMAIL_HOST_USER": credentials["smtp_username"],
            "EMAIL_HOST_PASSWORD": credentials["smtp_password"],
            "EMAIL_USE_TLS": "False",
            "EMAIL_USE_SSL": "False",
            "MAILGUN_API_KEY": "",
            "DJANGO_EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
            "DJANGO_DEFAULT_FROM_EMAIL": f"LLM Eval Lab <noreply@{credentials['sender_domain']}>",
            "ACCOUNT_EMAIL_VERIFICATION": "mandatory",
        },
    )
    # Keep the network override on future plain `docker compose` / `just up` calls.
    update_env(root / ".env", {"COMPOSE_FILE": os.pathsep.join(str(path) for path in files)})
    command = ["docker", "compose"]
    for path in files:
        command += ["-f", str(path)]
    subprocess.run([*command, "config", "--quiet"], cwd=root, check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [*command, "up", "-d", "--no-deps", "--no-build", "--force-recreate", "django", "celeryworker"],
        cwd=root,
        check=True,
    )
    write_file(
        state / "enabled.json",
        json.dumps({"app_root": str(root), "compose_files": [str(p) for p in files]}) + "\n",
    )
    print("Django and Celery now submit confirmation, password-reset and invitation emails through Postal.")
    print("Other app processes read the same settings on their next planned recreation.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=ROOT / "var/postal")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("setup", help="Create persistent services, credentials and DNS records")
    bootstrap.add_argument("--hostname", required=True)
    bootstrap.add_argument("--sender-domain", required=True)
    bootstrap.add_argument("--public-ip", required=True)
    bootstrap.add_argument("--admin-email")
    commands.add_parser("dns", help="Print generated public DNS records")
    commands.add_parser("verify", help="Check DNS ownership, authentication and PTR")
    commands.add_parser("status", help="Show service status")
    activate = commands.add_parser("configure-app", help="Verify DNS, switch Django SMTP and recreate web/Celery")
    activate.add_argument("--app-root", type=Path, default=ROOT)
    activate.add_argument("--environment", choices=["local", "production"], default="local")
    activate.add_argument(
        "--compose-file",
        action="append",
        default=[],
        help="Repeat for the base and existing host override files",
    )
    test = commands.add_parser("test-email", help="Send a test through the enabled application's real email backend")
    test.add_argument("recipient")
    args = parser.parse_args()
    state = args.state_dir.resolve()
    if args.command == "setup":
        setup(state, args)
    elif not (state / "credentials.json").is_file():
        msg = "Run setup first."
        raise ValueError(msg)
    elif args.command == "dns":
        print((state / "dns-records.md").read_text())
    elif args.command == "status":
        compose(state, "ps")
    elif args.command == "verify":
        return 0 if verify(state)["ready_for_delivery"] else 1
    elif args.command == "configure-app":
        configure_app(state, args)
    elif args.command == "test-email":
        enabled = json.loads((state / "enabled.json").read_text())
        if "@" not in args.recipient or args.recipient.startswith("-"):
            msg = "Provide a recipient email address."
            raise ValueError(msg)
        command = ["docker", "compose"]
        for path in enabled["compose_files"]:
            command += ["-f", path]
        subprocess.run(  # noqa: S603
            [*command, "exec", "-T", "django", "/entrypoint", "python", "manage.py", "sendtestemail", args.recipient],
            cwd=enabled["app_root"],
            check=True,
        )
        print("Test email accepted by Postal. Check the recipient inbox and Postal delivery logs for final delivery.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        # Captured Rails output may contain generated credentials; never dump it.
        message = (
            "Postal command failed; inspect the service logs."
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        print(message, file=sys.stderr)
        sys.exit(1)

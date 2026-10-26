"""Vendor a pinned, curated snapshot of semgrep-rules into the analyzer image.

Runs at image build time (the only moment the network is used). The rules
come from one commit of github.com/semgrep/semgrep-rules (Semgrep Rules
License v1.0, so they are fetched at build time and never committed to this
repository). Only rule files from the directories listed in ``PACKS`` are
kept, grouped into packs that the semgrep adapter selects with ``--config``.

The *content* of every pack is verified against ``EXPECTED_SHA256`` (sha256
over sorted relative path + bytes of the selected files), so the archive
format GitHub serves may change but the rules cannot: a different rule set
fails the build instead of silently changing measurements.

Each pack is then written as ONE JSON rule file (``<dest>/<pack>.json``):
pysemgrep spends ~170 ms per rule parsing YAML files (a 300-rule scan took
55 s, almost all of it loading config) but loads a JSON file in well under a
second. Rule ids are rewritten to the registry form ``<dir>.<file stem>.<id>``
(e.g. ``python.lang.security.audit.eval-detected.eval-detected``) so ids are
unique across packs and comparable with the semgrep registry; the adapter
passes ``--no-rewrite-rule-ids``.

Run with the semgrep venv's interpreter (ruamel.yaml is a semgrep dependency).
Usage: build_semgrep_rules.py <dest dir> [--print-hashes]
"""

# ruff: noqa: INP001, T201 - build/runtime script of the analyzer image, not an importable module

from __future__ import annotations

import hashlib
import io
import json
import re
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = "https://github.com/semgrep/semgrep-rules"
COMMIT = "9bbf021d0acffa3095fcffd17b2bc7787eb4796d"  # develop @ 2026-10-08T21:15:06Z
ARCHIVE_URL = f"{REPO}/archive/{COMMIT}.tar.gz"

# Curated packs. Security rules only (correctness/best-practice live in the
# linters); framework directories match the generated stacks (Flask, FastAPI,
# React, Vue); Django rules are left out because they duplicate the generic
# Python injection rules on non-Django code. "audit" rules (pattern-based,
# lower confidence) are split into their own packs so a profile can drop them.
PACKS: dict[str, dict[str, list[str]]] = {
    "python": {
        "include": [
            "python/lang/security",
            "python/flask/security",
            "python/fastapi/security",
            "python/sqlalchemy/security",
            "python/jwt/security",
            "python/requests/security",
            "python/cryptography/security",
            "python/pycryptodome/security",
            "python/jinja2/security",
            "python/pymongo/security",
        ],
        "audit": False,
    },
    "python-audit": {"include": [], "audit": True, "of": "python"},
    "javascript": {
        "include": [
            "javascript/lang/security",
            "javascript/browser/security",
            "javascript/express/security",
            "javascript/jsonwebtoken/security",
            "javascript/jose/security",
            "javascript/jwt-simple/security",
            "javascript/passport-jwt/security",
            "javascript/node-crypto/security",
            "javascript/sequelize/security",
            "javascript/shelljs/security",
            "javascript/serialize-javascript/security",
            "javascript/vm2/security",
            "javascript/sandbox/security",
            "javascript/vue/security",
            "typescript/lang/security",
            "typescript/react/security",
        ],
        "audit": False,
    },
    "javascript-audit": {"include": [], "audit": True, "of": "javascript"},
    "html": {"include": ["html/security"], "audit": None},
}

# sha256 of each pack's selected content (see pack_hash). Update deliberately
# (with --print-hashes) when COMMIT or PACKS change.
EXPECTED_SHA256: dict[str, str] = {
    "python": "6e741933531119453fca2461e8d001faa13e2495f2f4840fb371598f11a8531b",
    "python-audit": "0d648b683129cec71a44d2181ff52f3ee66d81cc8853c68575692e2f28b3dc50",
    "javascript": "e0e9956856ec4bc054b0b70a369f1744adca21a8fe3f44f42ee2cdbe77bc4961",
    "javascript-audit": "58bdcbc3924d3d05bd73a34f11072e01eb66822abc20a6ca1686997252f73e3d",
    "html": "94a6cbd323b70235172f57b6590d908cb5a2b4255430c8fa8ffc09f2e2ff1225",
}

_RULES_RE = re.compile(rb"^rules\s*:", re.MULTILINE)


def _is_audit(rel: str) -> bool:
    return "/audit/" in f"/{rel}"


def _is_rule_file(rel: str, data: bytes) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if not name.endswith((".yaml", ".yml")):
        return False
    # Test targets and autofix fixtures live next to the rules.
    if ".test." in name or ".fixed." in name:
        return False
    return bool(_RULES_RE.search(data))


def select(files: dict[str, bytes]) -> dict[str, dict[str, bytes]]:
    """Map pack name -> {relative rule path: bytes} for the curated directories."""
    packs: dict[str, dict[str, bytes]] = {name: {} for name in PACKS}
    for name, spec in PACKS.items():
        if spec.get("of"):
            continue
        for rel, data in files.items():
            if not any(rel.startswith(prefix.rstrip("/") + "/") for prefix in spec["include"]):
                continue
            if not _is_rule_file(rel, data):
                continue
            if spec["audit"] is None or not _is_audit(rel):
                packs[name][rel] = data
            else:
                packs[f"{name}-audit"][rel] = data
    return packs


def pack_hash(pack: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(pack):
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(pack[rel]).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest()


def to_json_pack(pack: dict[str, bytes]) -> tuple[bytes, int]:
    """One ``{"rules": [...]}`` JSON document with registry-style rule ids."""
    from ruamel.yaml import YAML  # the semgrep venv provides it

    loader = YAML(typ="safe")
    rules = []
    seen: set[str] = set()
    for rel in sorted(pack):
        doc = loader.load(pack[rel])
        prefix = rel.rsplit(".", 1)[0].replace("/", ".")
        for rule in doc["rules"]:
            rule_id = f"{prefix}.{rule['id']}"
            if rule_id in seen:
                msg = f"duplicate rule id {rule_id}"
                raise ValueError(msg)
            seen.add(rule_id)
            rules.append({**rule, "id": rule_id})
    # default=str: a few rules carry dates in their metadata.
    blob = json.dumps({"rules": rules}, sort_keys=True, separators=(",", ":"), default=str).encode()
    return blob, len(rules)


def _download() -> tuple[dict[str, bytes], str]:
    with urllib.request.urlopen(ARCHIVE_URL, timeout=120) as resp:  # noqa: S310 - fixed https URL
        blob = resp.read()
    files: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            # Strip the "semgrep-rules-<commit>/" top-level directory.
            rel = member.name.split("/", 1)[1] if "/" in member.name else member.name
            extracted = tar.extractfile(member)
            if extracted is not None:
                files[rel] = extracted.read()
    return files, hashlib.sha256(blob).hexdigest()


def main(argv: list[str]) -> int:
    dest = Path(argv[1])
    print_only = "--print-hashes" in argv
    files, archive_sha = _download()
    packs = select(files)
    manifest = {
        "repo": REPO,
        "commit": COMMIT,
        "archive_sha256": archive_sha,
        "license": "Semgrep Rules License v1.0",
        "packs": {},
    }
    failures = []
    dest.mkdir(parents=True, exist_ok=True)
    for name, pack in packs.items():
        digest = pack_hash(pack)
        expected = EXPECTED_SHA256.get(name)
        if not print_only and expected != digest:
            failures.append(f"{name}: expected {expected}, got {digest} ({len(pack)} files)")
        if not pack:
            failures.append(f"{name}: empty pack")
            continue
        blob, count = to_json_pack(pack)
        (dest / f"{name}.json").write_bytes(blob)
        manifest["packs"][name] = {
            "files": len(pack),
            "rules": count,
            "sha256": digest,
            "json_sha256": hashlib.sha256(blob).hexdigest(),
        }
    print(json.dumps(manifest, indent=2))
    if failures and not print_only:
        print("semgrep rule packs do not match the pinned content:\n" + "\n".join(failures), file=sys.stderr)
        return 1
    (dest / "rules-manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

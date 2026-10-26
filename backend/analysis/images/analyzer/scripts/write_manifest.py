"""Write /opt/analyzer/manifest.json: what is really inside this image.

Runs as the last build step. Every tool is executed once (``--version``) and
its real version must equal the pin declared in this directory (requirements
``*.in``, ``npm/package.json``, the binary pins passed as arguments); a
mismatch fails the build. The semgrep rule packs are loaded once against a
smoke file so a broken or Pro-only rule fails the build too, and their
content hashes are recorded. The vulture wrapper must make vulture ignore
``# noqa`` (it patches vulture internals; a vulture upgrade that breaks the
patch fails the build). The executor freezes this manifest into every plan
(tool versions, rule pack hashes, config hashes).

Usage: write_manifest.py <gitleaks version> <hadolint version>
"""

# ruff: noqa: INP001, T201 - build/runtime script of the analyzer image, not an importable module

from __future__ import annotations

import datetime
import hashlib
import json
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path("/opt/analyzer")
BIN = ROOT / "bin"
VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")

# slug -> (argv, pin source)
PYTHON_TOOLS = {
    "ruff": ["ruff", "--version"],
    "bandit": ["bandit", "--version"],
    "semgrep": ["semgrep", "--version"],
    "pylint": ["pylint", "--version"],
    "mypy": ["mypy", "--version"],
    "vulture": ["vulture", "--version"],
    "detect-secrets": ["detect-secrets", "--version"],
    "codespell": ["codespell", "--version"],
    "radon": ["radon", "--version"],
    "lizard": ["lizard", "--version"],
}
NODE_TOOLS = {
    "eslint": ["eslint", "--version"],
    "jscpd": ["jscpd", "--version"],
}
BINARY_TOOLS = {
    "gitleaks": ["gitleaks", "version"],
    "hadolint": ["hadolint", "--version"],
}
# Packages whose versions are provenance for a tool (plugins, parsers, engines).
NODE_COMPONENTS = {
    "eslint": [
        "eslint",
        "@eslint/js",
        "globals",
        "typescript-eslint",
        "@typescript-eslint/parser",
        "typescript",
        "eslint-plugin-security",
        "eslint-plugin-no-unsanitized",
        "eslint-plugin-vue",
        "vue-eslint-parser",
        "eslint-plugin-react-hooks",
    ],
    "jscpd": ["jscpd", "@jscpd/core", "@jscpd/finder", "@jscpd/tokenizer"],
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(argv: list[str]) -> str:
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [str(BIN / argv[0]), *argv[1:]],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        msg = f"{argv} exited {proc.returncode}: {proc.stderr.strip()[:2000]}"
        raise SystemExit(msg)
    return (proc.stdout or proc.stderr).strip()


def parse_version(output: str, prefix: str = "") -> str:
    text = output
    if prefix:
        # pylint/bandit print other component versions after their own.
        for line in output.splitlines():
            if line.lower().startswith(prefix):
                text = line
                break
    match = VERSION_RE.search(text)
    if not match:
        msg = f"no version in {output!r}"
        raise SystemExit(msg)
    return match.group(0)


def python_pins() -> dict[str, dict[str, str]]:
    pins: dict[str, dict[str, str]] = {}
    for req in sorted((ROOT / "requirements").glob("*.in")):
        entries = {}
        for raw in req.read_text().splitlines():
            line = raw.split("#", 1)[0].strip()
            if "==" in line:
                name, version = line.split("==", 1)
                entries[name.strip().lower()] = version.strip()
        pins[req.stem] = entries
    return pins


def node_versions() -> dict[str, str]:
    lock = json.loads((ROOT / "package-lock.json").read_text())
    out = {}
    for key, meta in lock.get("packages", {}).items():
        if key.startswith("node_modules/") and key.count("node_modules/") == 1:
            out[key.removeprefix("node_modules/")] = meta.get("version", "")
    return out


def semgrep_packs() -> dict[str, object]:
    rules_dir = ROOT / "rules" / "semgrep"
    manifest = json.loads((rules_dir / "rules-manifest.json").read_text())
    packs = sorted(manifest["packs"])
    smoke = ROOT / "scripts" / "smoke"
    argv = [
        "semgrep",
        "scan",
        "--json",
        "--metrics",
        "off",
        "--disable-version-check",
        "--oss-only",
        "--no-rewrite-rule-ids",
        "--time",
    ]
    for pack in packs:
        argv += ["--config", str(rules_dir / f"{pack}.json")]
    argv += [str(p) for p in sorted(smoke.iterdir())]
    result = json.loads(run(argv))
    errors = result.get("errors") or []
    if errors:
        msg = f"semgrep rule packs do not load cleanly: {json.dumps(errors)[:4000]}"
        raise SystemExit(msg)
    # time.rules lists the ids of the rules semgrep actually loaded.
    loaded = {r if isinstance(r, str) else r.get("id") for r in (result.get("time") or {}).get("rules") or []}
    declared: set[str] = set()
    for pack in packs:
        declared |= {r["id"] for r in json.loads((rules_dir / f"{pack}.json").read_text())["rules"]}
    manifest["rules_loaded"] = len(loaded)
    if loaded != declared:
        msg = (
            f"semgrep loaded {len(loaded)} rules, the packs hold {len(declared)}: "
            f"not loaded {sorted(declared - loaded)}, unexpected {sorted(loaded - declared)}, "
            f"skipped {json.dumps(result.get('skipped_rules'))[:2000]}"
        )
        raise SystemExit(msg)
    found = {r["check_id"] for r in result.get("results") or []}
    if not found:
        msg = "the semgrep smoke files produced no finding"
        raise SystemExit(msg)
    manifest["smoke_findings"] = sorted(found)
    return manifest


def check_vulture_ignores_noqa() -> None:
    """scripts/vulture_isolated.py must report dead code that carries a ``# noqa`` comment."""
    probe = Path(tempfile.mkdtemp(prefix="vulture-noqa-")) / "probe.py"
    probe.write_text("import json  # noqa: F401\n")
    proc = subprocess.run(  # noqa: S603 - fixed argv
        [
            str(ROOT / "venvs" / "vulture" / "bin" / "python"),
            "-I",
            str(ROOT / "scripts" / "vulture_isolated.py"),
            "--config",
            str(ROOT / "config" / "vulture.toml"),
            "--",
            str(probe),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode != 3 or "unused import 'json'" not in proc.stdout:
        msg = f"vulture_isolated.py did not disable '# noqa' (exit {proc.returncode}): {proc.stdout} {proc.stderr}"
        raise SystemExit(msg)


def main(argv: list[str]) -> int:
    gitleaks_pin, hadolint_pin = argv[1], argv[2]
    pins = python_pins()
    npm = node_versions()
    package_json = json.loads((ROOT / "package.json").read_text())
    tools: dict[str, dict[str, object]] = {}
    mismatches = []

    for slug, cmd in PYTHON_TOOLS.items():
        output = run(cmd)
        package = slug
        version = parse_version(output, prefix=slug if slug in {"pylint", "bandit"} else "")
        expected = pins.get(slug, {}).get(package)
        if expected != version:
            mismatches.append(f"{slug}: pinned {expected}, image has {version}")
        tools[slug] = {
            "version": version,
            "version_output": output[:500],
            "runtime": "python",
            "packages": pins.get(slug, {}),
            "lock_sha256": sha256_file(ROOT / "requirements" / f"{slug}.txt"),
        }
    for slug, cmd in NODE_TOOLS.items():
        output = run(cmd)
        version = parse_version(output)
        expected = package_json["dependencies"].get(slug)
        if expected != version:
            mismatches.append(f"{slug}: pinned {expected}, image has {version}")
        tools[slug] = {
            "version": version,
            "version_output": output[:500],
            "runtime": "node",
            "packages": {name: npm.get(name, "") for name in NODE_COMPONENTS[slug]},
            "lock_sha256": sha256_file(ROOT / "package-lock.json"),
        }
    for slug, cmd in BINARY_TOOLS.items():
        output = run(cmd)
        version = parse_version(output)
        expected = {"gitleaks": gitleaks_pin, "hadolint": hadolint_pin}[slug]
        if expected != version:
            mismatches.append(f"{slug}: pinned {expected}, image has {version}")
        tools[slug] = {
            "version": version,
            "version_output": output[:500],
            "runtime": "binary",
            "sha256": sha256_file(BIN / slug),
        }

    if mismatches:
        print("tool versions differ from the pins:\n" + "\n".join(mismatches), file=sys.stderr)
        return 1

    tools["semgrep"]["rules"] = semgrep_packs()
    check_vulture_ignores_noqa()
    configs = {p.name: sha256_file(p) for p in sorted((ROOT / "config").iterdir()) if p.is_file()}
    manifest = {
        "schema_version": 1,
        "built_at": datetime.datetime.now(tz=datetime.UTC).isoformat(timespec="seconds"),
        "release_cutoff": "2026-10-08",
        "runtime": {
            "python": platform.python_version(),
            "node": run(["node", "--version"]),
            "os": Path("/etc/debian_version").read_text().strip(),
            "uid": 10001,
        },
        "tools": tools,
        "configs": configs,
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps({slug: t["version"] for slug, t in tools.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

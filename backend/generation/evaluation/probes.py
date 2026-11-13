"""Probe catalogue and probe validity (spec 8.3).

* ``catalog()`` reads ``probes_catalog.yaml`` (generic probes with ``requires`` tags).
* ``applicable(content, existing)`` adds the generic probes of ``EvalSuite.probes=all_applicable``: those whose
  ``requires`` tags intersect the task's ``features``/``surfaces``, aimed at the endpoints the catalogue names.
* ``kind_validated(kind)`` says whether the kind has a passing mutant pair recorded by
  ``manage.py oracle_validate --kit`` in ``conformance/probe_validation.json`` for the current tester and probe
  rules. A kind without one is compiled ``disabled`` and every result of it is ``skipped(disabled)`` with the
  note ``probe_unvalidated``: ``not_exploited`` from an unvalidated probe could just mean the probe is broken.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Final

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "CATALOG_PATH",
    "VALIDATION_PATH",
    "applicable",
    "catalog",
    "catalog_rows",
    "kind_validated",
    "plaintext_password_database_verdict",
    "plaintext_password_verdict",
    "validation_record",
    "write_validation",
]

CATALOG_PATH: Final = Path(__file__).with_name("probes_catalog.yaml")
VALIDATION_PATH: Final = Path(__file__).parent / "conformance" / "probe_validation.json"


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict[str, Any]]:
    """``{kind: {cwe, description, requires, endpoints}}``."""
    data = yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}
    return {str(k): dict(v) for k, v in (data.get("kinds") or {}).items()}


def _rules() -> tuple[int, int]:
    from backend.generation.evaluation.tester import tester_module

    module = tester_module()
    return int(module.VERSION), int(module.PROBE_RULES_VERSION)


def validation_record(path: Path | None = None) -> dict[str, Any]:
    """The stored mutant-pair verdicts (``{}`` when ``oracle_validate --kit`` never ran)."""
    target = path or VALIDATION_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def kind_validated(kind: str, record: Mapping[str, Any] | None = None) -> bool:
    """True when ``kind`` has a passing mutant pair under the current tester and probe rules."""
    data = record if record is not None else validation_record()
    tester_version, probe_rules = _rules()
    if data.get("tester_version") != tester_version or data.get("probe_rules_version") != probe_rules:
        return False
    row = (data.get("kinds") or {}).get(kind) or {}
    return bool(row.get("validated"))


def write_validation(
    kinds: Mapping[str, Mapping[str, Any]],
    *,
    path: Path | None = None,
    at: str = "",
) -> dict[str, Any]:
    """Store the verdicts of one ``oracle_validate --kit`` run (``{kind: {mutant, fixed, validated}}``)."""
    tester_version, probe_rules = _rules()
    data = {
        "tester_version": tester_version,
        "probe_rules_version": probe_rules,
        "at": at,
        "kinds": {k: dict(v) for k, v in sorted(kinds.items())},
    }
    (path or VALIDATION_PATH).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return data


def catalog_rows() -> list[dict[str, Any]]:
    """``GET /eval/probes/catalog/``: ``[{kind, cwe, requires, description, validated}]``."""
    record = validation_record()
    return [
        {
            "kind": kind,
            "cwe": row["cwe"],
            "requires": row.get("requires") or {},
            "description": row.get("description") or "",
            "validated": kind_validated(kind, record),
        }
        for kind, row in sorted(catalog().items())
    ]


def _matches(row: Mapping[str, Any], content: Mapping[str, Any]) -> bool:
    req = row.get("requires") or {}
    features = set(content.get("features") or [])
    surfaces = set(content.get("surfaces") or [])
    return bool(features & set(req.get("features_any") or [])) or bool(surfaces & set(req.get("surfaces_any") or []))


def applicable(content: Mapping[str, Any], *, existing: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Generic probes for ``all_applicable`` that the task's own probes do not cover (same kind and endpoint)."""
    taken = {(str(p.get("kind")), p.get("endpoint")) for p in existing or []}
    out: list[dict[str, Any]] = []
    for kind, row in sorted(catalog().items()):
        if not _matches(row, content):
            continue
        selector = row.get("endpoints")
        if selector == "none" or not isinstance(selector, dict):
            if (kind, None) not in taken and not any(k == kind for k, _ in taken):
                out.append({"id": f"generic.{kind}", "cwe": row["cwe"], "kind": kind})
            continue
        for ep in content.get("endpoints") or []:
            if selector.get("methods") and ep["method"] not in selector["methods"]:
                continue
            if selector.get("auth") and ep["auth"] not in selector["auth"]:
                continue
            params: dict[str, Any] = {}
            if selector.get("query_semantics"):
                param = next(
                    (q["name"] for q in ep.get("query") or [] if q.get("semantics") in selector["query_semantics"]),
                    None,
                )
                if param is None:
                    continue
                params["param"] = param
            if selector.get("field"):
                params["field"] = selector["field"]
            if (kind, ep["id"]) in taken:
                continue
            out.append(
                {
                    "id": f"generic.{kind}.{ep['id']}",
                    "cwe": row["cwe"],
                    "kind": kind,
                    "endpoint": ep["id"],
                    "params": params,
                },
            )
    return out


def plaintext_password_verdict(files: Mapping[str, bytes], password: str) -> tuple[str, str, str | None]:
    """``(status, evidence, skip_reason)`` of ``plaintext_password_db`` from the app's data files (suite-side)."""
    if not password:
        return "skipped", "user_a never registered", "precondition_failed"
    if not files:
        return "skipped", "no data files to inspect", "precondition_failed"
    hit = next((name for name, data in sorted(files.items()) if password.encode() in data), None)
    if hit:
        return "exploited", f"user_a's password stored verbatim in {hit}", None
    return "not_exploited", f"{len(files)} data files, no plaintext password", None


def plaintext_password_database_verdict(
    tables: Mapping[str, bytes],
    files: Mapping[str, bytes],
    username: str,
    password: str,
) -> tuple[str, str, str | None]:
    """``plaintext_password_db`` on a ``database: postgres`` stack (suite-side).

    ``tables`` are the sidecar's rows (``runtime.split_pg_dump``: ``{"postgres:public.users": COPY text}``),
    ``files`` the app's data directory (a model may ignore ``DATABASE_URL`` and keep its users elsewhere).
    The password counts verbatim or hex-encoded (``bytea`` dumps as ``\\x<hex>``; hex is not a hash). A clean
    verdict needs evidence that user_a's account is stored at all: without user_a's username in a table or a
    data file the probe is ``skipped(precondition_failed)``, never ``not_exploited``.
    """
    if not password:
        return "skipped", "user_a never registered", "precondition_failed"
    sources = {**tables, **{f"data/{name}": data for name, data in files.items()}}
    if not sources:
        return "skipped", "the database dump has no rows and there are no data files", "precondition_failed"
    plain, hexed = password.encode(), password.encode().hex().encode()
    for name, data in sorted(sources.items()):
        if plain in data:
            return "exploited", f"user_a's password stored verbatim in {name}", None
        if hexed in data.lower():
            return "exploited", f"user_a's password stored hex-encoded in {name}", None
    holder = next((name for name, data in sorted(sources.items()) if username and username.encode() in data), None)
    if holder is None:
        return "skipped", "user_a's account not found in the database dump or the data files", "precondition_failed"
    return (
        "not_exploited",
        f"user_a's account is in {holder}; {len(tables)} tables, {len(files)} data files, no plaintext password",
        None,
    )

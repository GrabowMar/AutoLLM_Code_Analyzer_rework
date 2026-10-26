"""Every tool of the analyzer image on the canary fixtures (``-m docker``; skipped without a daemon).

Runs the real pinned image through the production sandbox (``sandbox.run_volume``
+ ``sandbox.run_tool``: read-only /src volume, cwd /tmp tmpfs, network none,
uid 10001, read-only rootfs, fresh /out volume) and checks:

* the sandbox contract the adapters rely on (user, read-only /src, writable
  /tmp and /out, no network);
* the image manifest: every tool's real version equals its spec pin;
* ``data/canary/expectations.yaml``: each tool finds its planted defects under
  vulnerable/, reports nothing counted under clean/ and reaches min_findings;
* isolation: app-shipped config files and inline suppressions change nothing.

The image must already be built (``manage.py analysis_build_image``); set
``LLMLAB_BUILD_ANALYZER_IMAGE=1`` to build it here (takes minutes, needs
network). Resources are labelled ``llmlab.analysis-dev`` so concurrent
processes reaping ``llmlab.analysis-run`` resources leave them alone; at most
one tool container runs at a time.
"""

from __future__ import annotations

import fnmatch
import os
import uuid

import pytest

from backend.analysis.engine import catalog
from backend.analysis.engine import sandbox
from backend.analysis.engine.adapters import get_adapter
from backend.analysis.engine.types import Invocation
from backend.analysis.engine.types import ToolFailure
from backend.analysis.tests.support import capture_tool_outputs as cap

pytestmark = pytest.mark.docker

STATIC_SLUGS = [s.slug for s in catalog.static_specs()]


@pytest.fixture(scope="module")
def image():
    from backend.runtime.services import docker_manager

    try:
        docker_manager.new_client().ping()
    except Exception as exc:  # noqa: BLE001 - any failure: no daemon for this suite
        pytest.skip(f"apps daemon unreachable: {exc}")
    build = os.environ.get("LLMLAB_BUILD_ANALYZER_IMAGE") == "1"
    try:
        tag, _image_id = sandbox.ensure_image(build=build)
    except sandbox.SandboxError as exc:
        pytest.skip(f"analyzer image not available: {exc}")
    return tag


def run(image: str, slug: str, files: dict[str, str], config: dict | None = None):
    """(sample, selected files, ToolParse) of one tool over *files* in the real image."""
    adapter = get_adapter(slug)
    sample = cap.make_sample(files)
    effective = adapter.effective_config(config or {})
    selected = adapter.select_files(sample, effective)
    assert selected, f"{slug}: nothing selected"
    invocation = adapter.invocation(selected, effective)
    run_id = f"pytest-toolchain-{uuid.uuid4().hex[:10]}"
    volume_files = {f.path: f.content for f in sample.files if f.included}
    with cap.dev_labels(), sandbox.run_volume(run_id, volume_files, image=image) as volume:
        outcome = sandbox.run_tool(image=image, volume=volume, invocation=invocation, run_id=run_id, tool_slug=slug)
    if outcome.infra_error:
        pytest.fail(f"{slug}: infrastructure error: {outcome.infra_error}")
    try:
        parse = adapter.interpret(outcome, selected, effective)
    except ToolFailure as exc:
        pytest.fail(f"{slug} failed on the canary: {exc.reason}: {exc.message}")
    return sample, selected, parse


def counted_categories(slug: str, sample, parse) -> list[tuple[str, str, str]]:
    """(path, rule, category) of the counted findings, normalized like the executor does."""
    from backend.analysis.engine import normalize

    out = []
    for raw in parse.findings:
        finding = normalize.normalize(slug, raw, sample, {})
        if finding.counted:
            out.append((finding.file_path, finding.rule_id, finding.category))
    return out


def test_sandbox_contract(image):
    probe = (
        "id -u; "
        "touch /src/x 2>/dev/null && echo src-writable || echo src-readonly; "
        "touch /tmp/x && echo tmp-writable; "
        "touch /out/x && echo out-writable; "
        "touch /opt/analyzer/x 2>/dev/null && echo rootfs-writable || echo rootfs-readonly; "
        "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)\" 2>/dev/null "
        "&& echo network || echo no-network; pwd"
    )
    run_id = f"pytest-toolchain-{uuid.uuid4().hex[:10]}"
    with cap.dev_labels(), sandbox.run_volume(run_id, {"a.py": b"x = 1\n"}, image=image) as volume:
        outcome = sandbox.run_tool(
            image=image,
            volume=volume,
            invocation=Invocation(argv=["sh", "-c", probe], timeout_s=60, mem_limit="128m"),
            run_id=run_id,
            tool_slug="probe",
        )
    assert outcome.exit_code == 0, outcome.stderr
    assert outcome.stdout.decode().split() == [
        "10001",
        "src-readonly",
        "tmp-writable",
        "out-writable",
        "rootfs-readonly",
        "no-network",
        "/tmp",  # noqa: S108 - the container's working directory
    ]


def test_image_manifest_matches_the_pins(image):
    manifest = sandbox.image_manifest(image)
    tools = manifest["tools"]
    for spec in catalog.static_specs():
        assert tools[spec.slug]["version"] == spec.version, spec.slug
    packs = tools["semgrep"]["rules"]["packs"]
    assert set(packs) == set(catalog.get_spec("semgrep").default_config["rulesets"])
    assert all(len(p["sha256"]) == 64 for p in packs.values())
    assert manifest["runtime"]["uid"] == 10001


@pytest.mark.parametrize("slug", STATIC_SLUGS)
def test_canary_expectations(image, slug):
    expectations = cap.canary_expectations()["tools"][slug]
    sample, _selected, parse = run(image, slug, cap.canary_files("vulnerable", "clean"))
    findings = [(f.file_path, f.rule_id) for f in parse.findings]
    missing = [
        entry
        for entry in expectations["must_find"]
        if not any(
            fnmatch.fnmatchcase(rule, entry["rule"]) and fnmatch.fnmatchcase(path, entry.get("path", "*"))
            for path, rule in findings
        )
    ]
    assert not missing, f"{slug} missed planted defects: {missing}; found {sorted(set(findings))}"
    positive = sum(1 for path, _ in findings if path.startswith("vulnerable/"))
    assert positive >= (expectations.get("min_findings") or 0)
    # Negative control: nothing at all under clean/ (stricter than the canary's counted categories) ...
    assert [f for f in findings if f[0].startswith("clean/")] == []
    # ... and the normalized view agrees.
    clean = set(expectations["clean_categories"])
    unexpected = [f for f in counted_categories(slug, sample, parse) if f[0].startswith("clean/") and f[2] in clean]
    assert unexpected == []
    assert parse.coverage["unparseable"] == []


@pytest.mark.parametrize("slug", STATIC_SLUGS)
def test_app_configs_and_suppressions_cannot_silence_a_tool(image, slug):
    _sample, _selected, plain = run(image, slug, cap.canary_files("vulnerable"))
    _sample, _selected, hostile = run(image, slug, {**cap.canary_files("vulnerable"), **cap.HOSTILE_CONFIG_FILES})
    canary = set(cap.canary_files("vulnerable"))

    def keyset(parse):
        return sorted((f.file_path, f.line, f.rule_id) for f in parse.findings if f.file_path in canary)

    assert keyset(hostile) == keyset(plain)
    assert keyset(plain)


SUPPRESSED_EXPECTED = {
    "ruff": {("suppressed/app.py", "F401"), ("suppressed/app.py", "S307"), ("suppressed/app.py", "S105")},
    "bandit": {("suppressed/app.py", "B307"), ("suppressed/app.py", "B105")},
    "pylint": {("suppressed/app.py", "unused-import"), ("suppressed/app.py", "eval-used")},
    "eslint": {("suppressed/run.js", "no-eval"), ("suppressed/Raw.vue", "vue/no-v-html")},
    "semgrep": {("suppressed/app.py", "python.lang.security.audit.eval-detected.eval-detected")},
    "gitleaks": {("suppressed/settings.py", "aws-access-token")},
    "detect-secrets": {("suppressed/settings.py", "AWS Access Key"), ("suppressed/app.py", "Secret Keyword")},
    "hadolint": {("suppressed/Dockerfile", "DL3007")},
    "flake8": {("suppressed/app.py", "F401")},
    "perflint": {("suppressed/perf.py", "use-list-copy")},
    "htmlhint": {("suppressed/page.html", "alt-require")},
    "stylelint": {("suppressed/site.css", "property-no-unknown")},
    "shellcheck": {("suppressed/run.sh", "SC2086")},
}


@pytest.mark.parametrize("slug", sorted(SUPPRESSED_EXPECTED))
def test_inline_suppressions_are_ignored(image, slug):
    _sample, _selected, parse = run(image, slug, cap.SUPPRESSED_FILES)
    found = {(f.file_path, f.rule_id) for f in parse.findings}
    assert SUPPRESSED_EXPECTED[slug] <= found


@pytest.mark.parametrize(
    "slug",
    [
        "ruff",
        "bandit",
        "pylint",
        "mypy",
        "vulture",
        "radon",
        "eslint",
        "flake8",
        "perflint",
        "deptry",
        "depcheck",
        "stylelint",
        "shellcheck",
    ],
)
def test_one_broken_file_does_not_hide_the_others(image, slug):
    files = cap.SYNTAX_FILES[slug if slug in cap.SYNTAX_FILES else "python"]
    _sample, _selected, parse = run(image, slug, files)
    assert parse.coverage["unparseable"]
    assert parse.coverage["files_scanned"] >= 1

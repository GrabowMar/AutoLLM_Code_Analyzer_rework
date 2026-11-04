"""The generation settings component and the v3 Python dependencies (SAMPLE_GEN_V3.md §2.2, §12.2).

- Every generation, agent and executor setting lives in ``config/settings/components/generation.py``,
  in per-WP blocks, and ``base.py`` only star-imports it.
- The v2 run defaults (budgets, sampling, provider routing, default stack) are gone: v3 takes them from
  seeded library rows, never from env.
- Storage roots under MEDIA_ROOT resolve at call time, so the per-test MEDIA_ROOT override applies.
- The v3 dependencies are declared, locked, and the tree-sitter grammars ship inside the wheel.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import tomllib
from pathlib import Path

import pytest
from django.conf import settings as django_settings

import config.settings.base as base_settings
from config.settings.components import generation as component

REPO = Path(__file__).resolve().parents[2]
BASE_PY = REPO / "config" / "settings" / "base.py"
COMPONENT_PY = REPO / "config" / "settings" / "components" / "generation.py"

GENERATION_PREFIXES = ("GENERATION_", "AGENT_", "EXECUTOR_")
REMOVED_V2_SETTINGS = (
    "GENERATION_DEFAULT_STACK",
    "AGENT_MAX_LLM_REQUESTS",
    "AGENT_WALL_CLOCK_SECONDS",
    "AGENT_MAX_TOTAL_TOKENS",
    "AGENT_MAX_COST_USD",
    "AGENT_SAMPLING_POLICY",
    "AGENT_DEFAULT_PROVIDER_ROUTING",
)
WP_MARKERS = ("01", "02a", "03", "05", "06", "07", "08", "09", "10", "11", "12", "13")
# Languages the symbol index and the pattern normalizer parse (§7.3, §7.9).
GRAMMARS = ("python", "javascript", "typescript", "tsx", "go", "java", "html", "css", "vue", "svelte")


def _top_level_names(path: Path) -> list[tuple[int, str]]:
    names: list[tuple[int, str]] = []
    for node in ast.parse(path.read_text()).body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        names.extend((node.lineno, t.id) for t in targets if isinstance(t, ast.Name))
    return names


def _load_component_copy(name: str):
    """Execute the component file as a fresh module (the environment is read at import)."""
    spec = importlib.util.spec_from_file_location(name, COMPONENT_PY)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestSettingsLayout:
    def test_base_defines_no_generation_settings(self):
        offenders = [name for _, name in _top_level_names(BASE_PY) if name.startswith(GENERATION_PREFIXES)]
        assert offenders == [], "generation settings belong in config/settings/components/generation.py"

    def test_base_star_imports_the_component(self):
        assert "from .components.generation import *" in BASE_PY.read_text()

    @pytest.mark.parametrize("name", REMOVED_V2_SETTINGS)
    def test_v2_run_defaults_are_removed(self, name):
        assert not hasattr(django_settings, name)
        assert not hasattr(component, name)

    @pytest.mark.parametrize(
        "name",
        [
            "GENERATION_BLOBS_ROOT",
            "GENERATION_RUNS_ROOT",
            "AGENT_SANDBOX_MEM_LIMIT_MAX",
            "AGENT_SANDBOX_NANO_CPUS_MAX",
            "AGENT_MAX_CONCURRENT_RUNS",
            "AGENT_OPENCODE_VERSION",
            "AGENT_MINI_SWE_AGENT_VERSION",
            "AGENT_AIDER_VERSION",
            "EXECUTOR_TICK_SECONDS",
            "EXECUTOR_LEASE_SECONDS",
            "APPS_DIND_MEM_LIMIT",
            "EVAL_SLOT_MEM",
        ],
    )
    def test_kept_settings_reach_django_settings(self, name):
        assert getattr(django_settings, name) is getattr(component, name)

    def test_component_helpers_do_not_shadow_base(self):
        # The star import must not rebind base.py's ``env`` or leak the private reader.
        assert "_env" not in vars(base_settings)
        assert base_settings.env is not component._env

    def test_every_setting_sits_in_a_wp_block_in_marker_order(self):
        lines = COMPONENT_PY.read_text().splitlines()
        markers = [(i + 1, m.group(1)) for i, line in enumerate(lines) if (m := re.match(r"# --- WP-(\w+) ---", line))]
        assert [wp for _, wp in markers] == list(WP_MARKERS)
        assert sum(line == "# --- end of WP blocks ---" for line in lines) == 1
        first_marker = markers[0][0]
        end_marker = lines.index("# --- end of WP blocks ---") + 1
        settings_lines = [(n, name) for n, name in _top_level_names(COMPONENT_PY) if name.isupper()]
        assert settings_lines
        assert all(first_marker < n < end_marker for n, _ in settings_lines), settings_lines

    def test_the_v2_machine_limit_aliases_are_gone(self):
        # Removed in wave 2 with their last reader (execution/capacity.py reads the *_MAX limits).
        assert not hasattr(django_settings, "AGENT_SANDBOX_MEM_LIMIT")
        assert not hasattr(django_settings, "AGENT_SANDBOX_NANO_CPUS")


class TestStorageRoots:
    @pytest.mark.parametrize(
        ("name", "subdir"),
        [("GENERATION_BLOBS_ROOT", "blobs"), ("GENERATION_RUNS_ROOT", "generation_runs")],
    )
    def test_root_follows_media_root_at_call_time(self, settings, tmp_path, name, subdir):
        settings.MEDIA_ROOT = str(tmp_path / "first")
        root = getattr(settings, name)
        assert Path(root) == tmp_path / "first" / subdir
        settings.MEDIA_ROOT = str(tmp_path / "second")
        assert Path(getattr(settings, name)) == tmp_path / "second" / subdir
        assert os.fspath(root) == str(tmp_path / "second" / subdir)
        assert str(root) == os.fspath(root)
        assert os.path.join(root, "x") == str(tmp_path / "second" / subdir / "x")  # noqa: PTH118

    def test_media_path_repr_names_the_subdirectory(self):
        assert repr(component.MediaPath("blobs")) == "MediaPath('blobs')"

    def test_env_pins_an_absolute_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GENERATION_BLOBS_ROOT", str(tmp_path / "blobs"))
        monkeypatch.setenv("GENERATION_RUNS_ROOT", str(tmp_path / "runs"))
        fresh = _load_component_copy("_generation_settings_env_probe")
        blobs_root, runs_root = fresh.GENERATION_BLOBS_ROOT, fresh.GENERATION_RUNS_ROOT
        assert blobs_root == str(tmp_path / "blobs")
        assert runs_root == str(tmp_path / "runs")

    def test_default_roots_are_media_paths(self, monkeypatch):
        monkeypatch.delenv("GENERATION_BLOBS_ROOT", raising=False)
        monkeypatch.delenv("GENERATION_RUNS_ROOT", raising=False)
        fresh = _load_component_copy("_generation_settings_default_probe")
        assert isinstance(fresh.GENERATION_BLOBS_ROOT, os.PathLike)
        assert isinstance(fresh.GENERATION_RUNS_ROOT, os.PathLike)


@pytest.fixture(scope="module")
def pyproject():
    return tomllib.loads((REPO / "pyproject.toml").read_text())


@pytest.fixture(scope="module")
def locked():
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    return {pkg["name"]: pkg["version"] for pkg in lock["package"]}


class TestDependencies:
    @staticmethod
    def _pins(requirements):
        pins = {}
        for req in requirements:
            name, _, version = req.partition("==")
            pins[re.sub(r"\[.*\]", "", name).lower()] = version
        return pins

    def test_v3_dependencies_are_declared_and_locked(self, pyproject, locked):
        project = pyproject["project"]
        runtime = self._pins(project["dependencies"])
        declared = {
            "tree-sitter": runtime.get("tree-sitter"),
            "tree-sitter-language-pack": runtime.get("tree-sitter-language-pack"),
            "zstandard": runtime.get("zstandard"),
            "factory-boy": self._pins(pyproject["dependency-groups"]["dev"]).get("factory-boy"),
            "tiktoken": self._pins(project["optional-dependencies"]["tokenizer"]).get("tiktoken"),
        }
        assert all(declared.values()), declared
        assert {name: locked.get(name) for name in declared} == declared

    def test_no_heavy_stats_dependencies(self, pyproject, locked):
        # D18: stats stay pure Python; mixed models are fitted offline.
        banned = {"statsmodels", "numpy", "scipy", "pandas", "pyarrow", "datasketch"}
        assert banned.isdisjoint(self._pins(pyproject["project"]["dependencies"]))
        assert banned.isdisjoint(locked)

    @pytest.mark.parametrize("language", GRAMMARS)
    def test_grammar_ships_inside_the_wheel(self, language):
        # tree-sitter-language-pack 1.x downloads grammars at first use; v3 needs them pinned and offline.
        import tree_sitter_language_pack as pack

        bindings = Path(pack.__file__).parent / "bindings"
        assert list(bindings.glob(f"{language}.*.so")), f"{language} grammar is not bundled"
        tree = pack.get_parser(language).parse(b"x = 1\n")
        assert tree.root_node is not None

    def test_zstandard_round_trip(self):
        import zstandard

        data = b"blob store payload " * 64
        compressed = zstandard.ZstdCompressor().compress(data)
        assert zstandard.ZstdDecompressor().decompress(compressed) == data

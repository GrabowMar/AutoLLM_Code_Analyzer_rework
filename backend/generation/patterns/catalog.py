"""The seed detector catalogue (``backend/generation/data/detectors/``, owned by WP-13).

Layout (the WP-04 YAML convention): ``detectors/<engine>/<slug>.yaml`` holds
``{name, description, content, is_archived?}`` with ``content`` a ``DetectorContent``; the slug is the
file stem. ``detectors/tests/<slug>/{positive,negative}[-N].<ext>`` are fixtures (spec 11.6; not entities,
the seeder skips ``tests/``): every positive file must be ``present`` at file scope, every negative one
``absent``. Probe detectors' fixtures are ``.json`` files ``{"probes": [ProbeResult, ...]}``.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from backend.generation.contracts.library import DetectorContent

DETECTORS_DIR = Path(__file__).resolve().parent.parent / "data" / "detectors"
FIXTURES_DIR = DETECTORS_DIR / "tests"
_EXT_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
    ".svelte": "svelte",
    ".go": "go",
    ".java": "java",
    ".json": "json",
}


@dataclass(frozen=True)
class Fixture:
    path: Path
    language: str
    expected: str  # present | absent

    @property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")


@dataclass
class SeedDetector:
    slug: str
    name: str
    description: str
    content: dict[str, Any]
    is_archived: bool = False
    fixtures: list[Fixture] = field(default_factory=list)


def language_of(path: Path) -> str:
    return _EXT_LANGUAGE.get(path.suffix.lower(), path.suffix.lower().lstrip("."))


def fixtures_for(slug: str, root: Path = FIXTURES_DIR) -> list[Fixture]:
    """``<root>/<slug>/positive[-N].<ext>`` (present) and ``negative[-N].<ext>`` (absent), sorted."""
    out: list[Fixture] = []
    directory = root / slug
    if not directory.is_dir():
        return out
    for expected, prefix in (("present", "positive"), ("absent", "negative")):
        out.extend(
            Fixture(path=p, language=language_of(p), expected=expected)
            for p in sorted(directory.iterdir())
            if p.is_file() and p.name.split(".", 1)[0].split("-", 1)[0] == prefix
        )
    return out


def load_seed_detectors(root: Path = DETECTORS_DIR) -> list[SeedDetector]:
    """Every seed detector, validated and normalized (``DetectorContent`` dump), with its fixtures."""
    out: list[SeedDetector] = []
    for path in sorted(root.glob("*/*.yaml")):
        if path.parent.name == "tests":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        content = DetectorContent.model_validate(data["content"]).model_dump(mode="json", by_alias=True)
        if content["engine"] != path.parent.name:
            msg = f"{path}: engine {content['engine']!r} does not match its folder {path.parent.name!r}"
            raise ValueError(msg)
        out.append(
            SeedDetector(
                slug=path.stem,
                name=str(data.get("name") or path.stem),
                description=str(data.get("description") or ""),
                content=content,
                is_archived=bool(data.get("is_archived", False)),
                fixtures=fixtures_for(path.stem, root / "tests"),
            ),
        )
    return out

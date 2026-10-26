"""The adapter regression cases in the real analyzer image (``-m docker``; skipped without a daemon).

Same cases and expectations as ``test_adapters_regressions.py`` (whose recorded outputs were produced
with the pinned tools outside the image), plus the mypy pre-filter and the ESLint language-parity
corpus, run through the production sandbox.
"""

from __future__ import annotations

import pytest

from backend.analysis.tests.integration.test_toolchain_docker import image  # noqa: F401 - pytest fixture
from backend.analysis.tests.integration.test_toolchain_docker import run
from backend.analysis.tests.test_adapters_regressions import CASES
from backend.analysis.tests.test_adapters_regressions import CHECKS

pytestmark = pytest.mark.docker

RETURN_ERROR = 'def add(a: int, b: int) -> int:\n    return "x"\n'


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_regression_case_in_the_image(image, name):  # noqa: F811 - the imported fixture
    entry = CASES[name]
    _sample, selected, parse = run(image, entry["slug"], entry["files"], entry["config"])
    assert [f.path for f in selected] == entry["selected"]
    CHECKS[name](parse)


@pytest.mark.parametrize(
    ("files", "broken", "checked"),
    [
        (
            {
                "app.py": "from models import User\n\n\n" + RETURN_ERROR,
                "models.py": "class User:\n    def f(self, x:\n",
            },
            "models.py",
            "app.py",
        ),
        (
            {"app.py": "import helpers\n\n\n" + RETURN_ERROR, "helpers.py": "def f():\n    pass\nbreak\n"},
            "helpers.py",
            "app.py",
        ),
        ({"models.py": 'role = "admin"  # type: list of user ids\n', "ok.py": RETURN_ERROR}, None, "ok.py"),
        ({"app.py": "# mypy: ignore-errors\n" + RETURN_ERROR}, None, "app.py"),
    ],
)
def test_mypy_converges_in_the_image(image, files, broken, checked):  # noqa: F811 - the imported fixture
    _sample, _selected, parse = run(image, "mypy", files)
    assert [u["path"] for u in parse.coverage["unparseable"]] == ([broken] if broken else [])
    assert (checked, "return-value") in {(f.file_path, f.rule_id) for f in parse.findings}


PARITY_JS = """export function f(a, b) {
  let x = 1;
  if (a = b) { }
  const o = { k: 1, k: 2 };
  switch (a) { case 1: x++; case 2: x--; }
  x = x;
  var y = o;
  return x + y;
  console.log("dead");
}
"""
REACT = """import React from "react";
import Header from "./Header";

export default function App() {
  return <Header title="Hi" />;
}
"""
VUE_TS = """<template>
  <button @click="toggle(1)">{{ label }}</button>
</template>

<script setup lang="ts">
enum Status { Idle, Busy }
interface Api {
  get(url: string): Promise<unknown>;
  onChange: (value: string) => void;
}
const props = defineProps<{ label: string; api: Api }>();
const emit = defineEmits<{ (e: "toggle", id: number): void }>();
let timer: NodeJS.Timeout | null = null;
const init: RequestInit = { method: "GET" };
let state = Status.Idle;
const unusedThing = 1;
function toggle(id: number) {
  emit("toggle", id);
  timer = setTimeout(() => props.api.get("/x"), 10);
  state = Status.Busy;
  return [init, state];
}
</script>
"""
VUE_JS = "<template>\n  <p>{{ count }}</p>\n</template>\n\n<script setup>\nconst count = ref(0);\n</script>\n"


def _family(rule: str) -> str:
    return rule.removeprefix("@typescript-eslint/")


def test_eslint_rule_semantics_do_not_depend_on_the_language(image):  # noqa: F811 - the imported fixture
    files = {
        "p/same.js": PARITY_JS,
        "p/same.ts": PARITY_JS,
        "p/App.jsx": REACT,
        "p/App.tsx": REACT,
        "p/Timer.vue": VUE_TS,
        "p/Count.vue": VUE_JS,
    }
    _sample, _selected, parse = run(image, "eslint", files)
    found: dict[str, set[tuple[int | None, str]]] = {}
    for finding in parse.findings:
        found.setdefault(finding.file_path, set()).add((finding.line, _family(finding.rule_id)))
    assert found["p/same.js"] == found["p/same.ts"]
    assert {"no-cond-assign", "no-dupe-keys", "no-unreachable"} <= {rule for _, rule in found["p/same.ts"]}
    assert found["p/App.jsx"] == found["p/App.tsx"] == {(1, "no-unused-vars")}
    # TypeScript in a Vue SFC: no core no-undef on types, no unused-vars on type-only names.
    assert {rule for _, rule in found["p/Timer.vue"]} == {"no-unused-vars"}
    assert {line for line, _ in found["p/Timer.vue"]} == {13, 16}
    # Plain JavaScript in a Vue SFC keeps no-undef (ref is not imported).
    assert (6, "no-undef") in found["p/Count.vue"]

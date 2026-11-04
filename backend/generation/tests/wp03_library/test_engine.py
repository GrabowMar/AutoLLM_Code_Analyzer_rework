"""The sandboxed renderer: determinism, normalization, StrictUndefined, the sandbox and source spans."""

from __future__ import annotations

import pytest

from backend.generation import identity
from backend.generation.prompts.engine import FILTERS
from backend.generation.prompts.engine import Owner
from backend.generation.prompts.engine import RenderedText
from backend.generation.prompts.engine import RenderError
from backend.generation.prompts.engine import SourcedText
from backend.generation.prompts.engine import Stimulus
from backend.generation.prompts.engine import find_variables
from backend.generation.prompts.engine import preview_prompt_hash
from backend.generation.prompts.engine import render
from backend.generation.prompts.engine import render_set

TASK = Owner("todo@1#aaaaaaaaaaaa", "task", "todo@1#aaaaaaaaaaaa")
STACK = Owner("stack-frag@1#bbbbbbbbbbbb", "stack", "flask-react@1#cccccccccccc", "fragment")


def _tpl(template: str, variables: tuple[str, ...] = ("x",), role: str = "brief") -> dict:
    return {
        "role": role,
        "engine": "jinja2-sandbox@1",
        "template": template,
        "variables": list(variables),
        "modes": ["app"],
    }


def assert_partition(rendered: RenderedText) -> None:
    """The spans are contiguous, non-overlapping and cover the whole text."""
    cursor = 0
    for span in rendered.sources:
        assert span.char_start == cursor
        assert span.char_end > span.char_start
        cursor = span.char_end
    assert cursor == len(rendered.text)


class TestOutput:
    def test_identical_bytes_and_spans_across_runs(self):
        context = {"x": SourcedText("Todo", TASK), "items": [SourcedText("a", TASK), SourcedText("b", TASK)]}
        template = _tpl("# {{ x }}\n{% for i in items %}- {{ i }}\n{% endfor %}", ("x", "items"))
        first = render(template, context)
        second = render(template, context)
        assert first == second
        assert first.text.encode() == second.text.encode()

    def test_nfc_lf_and_exactly_one_trailing_newline(self):
        rendered = render(_tpl("Café\r\nline two\r\n\n\n{{ x }}\n\n"), {"x": ""})
        assert rendered.text == "Café\nline two\n"
        assert render(_tpl("no newline"), {}).text == "no newline\n"
        assert render(_tpl(""), {}).text == "\n"
        assert_partition(render(_tpl(""), {}))

    def test_sourced_value_with_crlf_is_normalized(self):
        rendered = render(_tpl("A {{ x }} B"), {"x": SourcedText("one\r\ntwo", TASK)})
        assert rendered.text == "A one\ntwo B\n"
        assert_partition(rendered)
        owned = [s for s in rendered.sources if s.owner_kind == "task"]
        assert rendered.text[owned[0].char_start : owned[0].char_end] == "one\ntwo"

    def test_whitespace_control(self):
        rendered = render(_tpl("{% if x %}\n  yes\n{% endif %}\nend"), {"x": True})
        assert rendered.text == "  yes\nend\n"


class TestStrictness:
    def test_undefined_variable_errors(self):
        with pytest.raises(RenderError) as excinfo:
            render(_tpl("{{ x }}"), {})
        assert excinfo.value.kind == "undefined"

    def test_undeclared_variable_is_not_passed_in(self):
        with pytest.raises(RenderError):
            render(_tpl("{{ y }}", ("x",)), {"x": "1", "y": "2"})

    def test_missing_key_errors(self):
        with pytest.raises(RenderError):
            render(_tpl("{{ x.nope }}"), {"x": {"title": "a"}})

    def test_default_filter_handles_undefined(self):
        assert render(_tpl("[{{ x.nope | default('none') }}]"), {"x": {}}).text == "[none]\n"

    def test_syntax_error(self):
        with pytest.raises(RenderError) as excinfo:
            render(_tpl("{% if x %}unterminated"), {"x": 1})
        assert excinfo.value.kind == "syntax"

    def test_unknown_engine(self):
        with pytest.raises(RenderError):
            render({**_tpl("x"), "engine": "jinja2"}, {})


class TestSandbox:
    @pytest.mark.parametrize(
        "template",
        [
            "{{ x.__class__ }}",
            "{{ x.upper() }}",
            "{{ x.__class__.__mro__ }}",
            "{{ x['__class__'] }}",
            "{{ items.append }}",
        ],
    )
    def test_attribute_access_is_blocked(self, template):
        with pytest.raises(RenderError) as excinfo:
            render(_tpl(template, ("x", "items")), {"x": "text", "items": []})
        assert excinfo.value.kind in {"security", "undefined"}

    def test_mapping_items_are_reachable(self):
        assert render(_tpl("{{ x.title }} {{ x['title'] }}"), {"x": {"title": "T"}}).text == "T T\n"

    def test_loop_variable_works(self):
        rendered = render(_tpl("{% for i in x %}{{ loop.index }}{% endfor %}"), {"x": ["a", "b"]})
        assert rendered.text == "12\n"

    @pytest.mark.parametrize("template", ["{{ x | safe }}", "{{ x | e }}", "{{ x | attr('upper') }}"])
    def test_filters_outside_the_whitelist_are_refused(self, template):
        with pytest.raises(RenderError):
            render(_tpl(template), {"x": "a"})

    def test_include_and_import_fail(self):
        with pytest.raises(RenderError):
            render(_tpl("{% include 'other' %}"), {})

    def test_only_range_is_global(self):
        assert render(_tpl("{% for i in range(3) %}{{ i }}{% endfor %}"), {}).text == "012\n"
        with pytest.raises(RenderError):
            render(_tpl("{{ cycler }}", ("cycler",)), {})

    def test_reserved_sentinels_are_refused_in_templates(self):
        with pytest.raises(RenderError):
            render(_tpl("bad  char"), {})


class TestSpans:
    def test_literal_text_belongs_to_the_template_and_values_to_their_owner(self):
        rendered = render(_tpl("Build {{ x }} now.", ("x",)), {"x": SourcedText("Todo", TASK)}, template_ref="brief@1")
        kinds = [(rendered.text[s.char_start : s.char_end], s.owner_kind) for s in rendered.sources]
        assert kinds == [("Build ", "prompt"), ("Todo", "task"), (" now.\n", "prompt")]
        assert_partition(rendered)

    def test_nested_fragment_spans_are_kept(self):
        fragment = render(_tpl("Use Flask for {{ x }}.", ("x",)), {"x": SourcedText("todos", TASK)}, owner=STACK)
        value = SourcedText.from_rendered(fragment, STACK)
        rendered = render(_tpl("Stack: {{ s }}", ("s",)), {"s": value})
        pieces = [(rendered.text[s.char_start : s.char_end], s.owner_kind) for s in rendered.sources]
        assert pieces == [
            ("Stack: ", "prompt"),
            ("Use Flask for ", "stack"),
            ("todos", "task"),
            (".", "stack"),
            ("\n", "prompt"),
        ]
        assert_partition(rendered)

    def test_filters_keep_the_owner_of_sourced_input(self):
        rendered = render(
            _tpl("{{ x | upper }}|{{ items | join(', ') }}", ("x", "items")),
            {
                "x": SourcedText("todo", TASK),
                "items": [SourcedText("a", TASK), SourcedText("b", TASK)],
            },
        )
        owned = [rendered.text[s.char_start : s.char_end] for s in rendered.sources if s.owner_kind == "task"]
        assert owned == ["TODO", "a, b"]

    def test_mixed_owners_fall_back_to_the_template(self):
        other = Owner("x@1#dddddddddddd", "style", "x@1#dddddddddddd")
        rendered = render(
            _tpl("{{ items | join(', ') }}", ("items",)),
            {"items": [SourcedText("a", TASK), SourcedText("b", other)]},
        )
        assert [s.owner_kind for s in rendered.sources] == ["prompt"]

    def test_adjacent_spans_of_one_owner_merge(self):
        rendered = render(
            _tpl("{{ a }}{{ b }}", ("a", "b")),
            {"a": SourcedText("x", TASK), "b": SourcedText("y", TASK)},
        )
        assert [(s.owner_kind, s.char_start, s.char_end) for s in rendered.sources] == [
            ("task", 0, 2),
            ("prompt", 2, 3),
        ]


class TestFilters:
    def test_whitelist_matches_the_spec(self):
        assert set(FILTERS) == {
            "indent",
            "join",
            "upper",
            "lower",
            "title",
            "trim",
            "wordwrap",
            "replace",
            "length",
            "sort",
            "default",
            "tojson",
            "bullet_list",
            "endpoint_table",
        }

    def test_endpoint_table_and_bullet_list(self):
        endpoints = [{"id": "a", "method": "GET", "path": "/api/a", "auth": "user"}]
        text = render(
            _tpl("{{ x | endpoint_table }}\n{{ y | bullet_list }}", ("x", "y")),
            {"x": endpoints, "y": ["one", "two"]},
        ).text
        assert "| GET | /api/a | signed-in users |" in text
        assert text.endswith("- one\n- two\n")

    def test_sort_tojson_indent(self):
        text = render(
            _tpl("{{ x | sort | join(',') }} {{ y | tojson }} {{ z | indent(2) }}", ("x", "y", "z")),
            {
                "x": ["b", "A", "c"],
                "y": {"b": 1, "a": "é"},
                "z": "l1\nl2",
            },
        ).text
        assert text == 'A,b,c {"a": "é", "b": 1} l1\n  l2\n'


def test_find_variables():
    assert find_variables("{{ task.title }}{% for e in task.endpoints %}{{ e }}{% endfor %}{{ range(2) }}") == {"task"}


def test_render_set_orders_roles_and_uses_drafts():
    templates = {
        "brief@1#aaaaaaaaaaaa": _tpl("Brief {{ x }}"),
        "sys@1#aaaaaaaaaaaa": _tpl("System", (), role="system"),
    }
    prompt_set = {"mode": "app", "roles": {"brief": "brief@1#aaaaaaaaaaaa", "system": "sys@1#aaaaaaaaaaaa"}}
    stimuli = render_set(prompt_set, {"x": "y"}, loader=templates.__getitem__)
    assert [(s.role, s.text) for s in stimuli] == [("system", "System\n"), ("brief", "Brief y\n")]
    drafted = render_set(
        prompt_set,
        {"x": "y"},
        loader=templates.__getitem__,
        templates={"brief": {"template": "Draft {{ x }}"}},
    )
    assert drafted[1].text == "Draft y\n"
    assert drafted[1].template_ref == "draft:brief"


def test_preview_prompt_hash_goes_through_compute_keys():
    stimuli = [Stimulus("brief", "b@1", "hello\n"), Stimulus("system", "s@1", "sys\n")]
    expected = identity.H(sorted([["brief", identity.text_sha("hello\n")], ["system", identity.text_sha("sys\n")]]))
    assert preview_prompt_hash(stimuli) == expected
    assert preview_prompt_hash(list(reversed(stimuli))) == expected


def test_stimulus_payload():
    payload = Stimulus("brief", "b@1", "a" * 36).as_dict()
    assert payload["chars"] == 36
    assert payload["est_tokens"] == 10
    assert payload["sha"] == identity.text_sha("a" * 36)

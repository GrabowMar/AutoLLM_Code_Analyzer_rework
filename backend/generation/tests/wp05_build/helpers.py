"""Helpers shared by the WP-05 tests."""

from __future__ import annotations

from backend.generation.build import starters


def phase_a_entries() -> list[starters.ComposeEntry]:
    return [entry for entry in starters.compose_plan() if entry.phase == "A"]


def compose_entry(entry: starters.ComposeEntry) -> starters.ComposedStarter:
    """Compose ``entry`` in memory against its stack file, with a fixed fake pinned stack ref."""
    stack = starters.load_stack_yaml(entry.stack)
    return starters.compose_tree(
        entry.stack,
        stack["content"],
        entry.level,
        stack_ref=f"{entry.stack}@1#{'0' * 12}",
        stack_name=stack["name"],
    )

"""Python validation helpers for copilot mode."""

from __future__ import annotations

import ast as ast_module
import re


def validate_python_code(code: str) -> list[str]:
    """Validate Python code and return list of error descriptions."""
    errors: list[str] = []
    if not code or not code.strip():
        errors.append("Empty code output")
        return errors

    try:
        ast_module.parse(code)
    except SyntaxError as e:
        errors.append(f"SyntaxError at line {e.lineno}: {e.msg}")
        return errors

    if "pass" in code:
        stubs = re.findall(
            r"def\s+\w+\s*\([^)]*\)\s*:\s*\n\s+(?:pass|\.\.\.)\s*$",
            code,
            re.MULTILINE,
        )
        if len(stubs) > 2:
            errors.append(f"{len(stubs)} stub functions with only pass/...")

    if len(code.strip().split("\n")) < 30:
        errors.append(
            f"Code too short ({len(code.strip().split(chr(10)))} lines); "
            "expected 100+ lines for a complete app",
        )

    return errors

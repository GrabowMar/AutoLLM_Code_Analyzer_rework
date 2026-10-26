"""Run mypy on the files it can check; report the ones it cannot.

mypy aborts the whole check (exit 2, nothing but that file's errors) when one
module it reads cannot be parsed, and truncated or broken LLM output makes
that common. This wrapper makes the check converge instead:

1. Every selected file is parsed and compiled with the interpreter mypy runs
   on, for the target ``--python-version``. A file that fails is
   ``unparseable`` and is not passed to mypy. Compiling also catches code that
   parses but cannot run ('break' outside a loop, 'yield' outside a
   function...), which mypy treats as blocking errors too.
2. mypy follows imports into modules it was not given (``follow_imports =
   silent``), so ``from models import User`` would still make it read a broken
   ``models.py``. The run uses a derived config — the baked one plus
   ``follow_imports = skip`` for the module of every Python file under the
   sample root that does not compile — so such imports are ``Any``.
3. ``# mypy:`` inline configuration comments (``# mypy: ignore-errors``...)
   would let the app configure its own type check. When a checked file has
   one, mypy runs on staged copies of the sample's Python files in which those
   lines are neutralized (line numbers kept); paths are mapped back.
4. If mypy still exits 2 with nothing but syntax errors in sample files (a
   blocking error the steps above did not predict), those files become
   ``unparseable`` (or skipped imports) and mypy runs again, a bounded number
   of times.

The last run's stdout, stderr and exit status are passed through unchanged
(paths mapped back to the sample root), so they are mypy's own.

Usage: mypy_prefilter.py <report.json> <python version> <mypy args incl. --config-file=...> -- <files...>
Environment: MYPYPATH = the sample root (module names are relative to it).
Report: {"python", "target", "checked": [paths], "unparseable": [{path, reason, line}],
         "skipped_imports": [modules], "inline_config_neutralized": [paths], "runs": n}
"""

# ruff: noqa: INP001, T201, PTH100, PTH103, PTH109, PTH118, PTH120 - image runtime script (string paths)

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import warnings
from pathlib import Path

PY_SUFFIXES = (".py", ".pyi")
# mypy (util.get_mypy_comments) reads inline configuration from lines starting with exactly this.
INLINE_PREFIX = b"# mypy: "
INLINE_NEUTRALIZED = b"# mypy-inline-config-ignored: "
CONFIG_FLAG = "--config-file"
# Runs of mypy in total (the first one plus retries after unpredicted blocking syntax errors).
MAX_RUNS = 4
EXIT_BLOCKED = 2


def check(path: str, target: tuple[int, int]) -> dict[str, object] | None:
    """None when *path* parses and compiles for the target version, else the reason."""
    try:
        source = Path(path).read_bytes()
    except OSError as exc:
        return {"path": path, "reason": f"unreadable: {exc.strerror or exc}", "line": None}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # SyntaxWarnings are not errors (and not ours to print)
            # bytes: the PEP 263 coding cookie is honoured, as mypy does.
            tree = ast.parse(source, filename=path, feature_version=target)
            compile(tree, path, "exec", dont_inherit=True)
    except SyntaxError as exc:
        return {"path": path, "reason": f"{exc.msg}", "line": exc.lineno}
    except (ValueError, RecursionError, MemoryError) as exc:
        return {"path": path, "reason": f"{type(exc).__name__}: {exc}", "line": None}
    return None


def module_name(path: str, root: str) -> str | None:
    """The module mypy derives for *path* below *root* (explicit package bases), None if not importable."""
    rel = os.path.relpath(path, root)
    if rel.startswith(".."):
        return None
    parts = rel.replace(os.sep, "/").split("/")
    for suffix in PY_SUFFIXES:
        if parts[-1].endswith(suffix):
            parts[-1] = parts[-1][: -len(suffix)]
            break
    else:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts)


def python_files(root: str) -> list[str]:
    """Every .py/.pyi file below *root* (the modules mypy can import)."""
    found = []
    for directory, _dirs, names in os.walk(root):
        found += [os.path.join(directory, n) for n in names if n.endswith(PY_SUFFIXES)]
    return sorted(found)


def split_config(mypy_args: list[str]) -> tuple[str, list[str]]:
    """(--config-file value, the other mypy args)."""
    config, rest = "", []
    args = iter(mypy_args)
    for arg in args:
        if arg.startswith(f"{CONFIG_FLAG}="):
            config = arg.split("=", 1)[1]
        elif arg == CONFIG_FLAG:
            config = next(args, "")
        else:
            rest.append(arg)
    return config, rest


def write_config(base: str, skip_modules: set[str], directory: str) -> str:
    """The baked config plus ``follow_imports = skip`` for every module in *skip_modules*."""
    text = Path(base).read_text()
    sections = "".join(f"\n[mypy-{module}]\nfollow_imports = skip\n" for module in sorted(skip_modules))
    path = os.path.join(directory, "mypy.ini")
    Path(path).write_text(text.rstrip("\n") + "\n" + sections)
    return path


def has_inline_config(path: str) -> bool:
    try:
        return any(line.startswith(INLINE_PREFIX) for line in Path(path).read_bytes().split(b"\n"))
    except OSError:
        return False


def stage(root: str, directory: str) -> str:
    """Copies of every Python file below *root* with ``# mypy:`` lines neutralized (line numbers kept)."""
    staged = os.path.join(directory, "src")
    for path in python_files(root):
        target = os.path.join(staged, os.path.relpath(path, root))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        lines = Path(path).read_bytes().split(b"\n")
        rewritten = [
            INLINE_NEUTRALIZED + line[len(INLINE_PREFIX) :] if line.startswith(INLINE_PREFIX) else line
            for line in lines
        ]
        Path(target).write_bytes(b"\n".join(rewritten))
    return staged


def blocking_syntax_errors(stdout: str, root: str) -> dict[str, dict[str, object]] | None:
    """{path: first error} when a run printed nothing but syntax errors in files below *root*, else None."""
    blamed: dict[str, dict[str, object]] = {}
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            return None
        if not isinstance(item, dict):
            return None
        if item.get("severity") == "note":
            continue
        if item.get("severity") != "error" or item.get("code") != "syntax":
            return None
        path = os.path.abspath(str(item.get("file") or ""))
        if not path.startswith(root.rstrip(os.sep) + os.sep):
            return None
        blamed.setdefault(path, item)
    return blamed or None


def run_mypy(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    # mypy's console script of this interpreter's environment (cwd is never on its sys.path).
    script = Path(sys.executable).with_name("mypy")
    command = [str(script)] if script.is_file() else [sys.executable, "-m", "mypy"]
    return subprocess.run(  # noqa: S603 - fixed executable, file paths as arguments
        [*command, *argv],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def main(argv: list[str]) -> int:
    report, version, rest = argv[1], argv[2], argv[3:]
    if "--" not in rest:
        print("usage: mypy_prefilter.py <report> <version> <mypy args...> -- <files...>", file=sys.stderr)
        return EXIT_BLOCKED
    split = rest.index("--")
    base_config, mypy_args = split_config(rest[:split])
    files = rest[split + 1 :]
    if not base_config:
        print(f"mypy_prefilter.py: {CONFIG_FLAG}=<path> is required", file=sys.stderr)
        return EXIT_BLOCKED
    major, minor = (int(part) for part in version.split(".")[:2])
    target = (major, minor)
    root = os.path.abspath(os.environ.get("MYPYPATH") or os.getcwd())

    checked, unparseable = [], []
    for path in files:
        problem = check(path, target)
        if problem is None:
            checked.append(path)
        else:
            unparseable.append(problem)
    # Modules mypy must not follow into: every Python file under the root that does not compile.
    selected = set(files)
    skip_modules = set()
    broken = {os.path.abspath(str(u["path"])) for u in unparseable}
    broken |= {p for p in python_files(root) if p not in selected and check(p, target) is not None}
    for path in broken:
        module = module_name(path, root)
        if module:
            skip_modules.add(module)

    neutralized = [path for path in checked if has_inline_config(path)]
    workdir = tempfile.mkdtemp(prefix="llmlab-mypy-")
    run_root = stage(root, workdir) if neutralized else root
    env = {**os.environ, "MYPYPATH": run_root}

    def to_run(path: str) -> str:
        return os.path.join(run_root, os.path.relpath(os.path.abspath(path), root)) if neutralized else path

    # mypy prints a path below its working directory relative to it (the image runs it from /tmp,
    # where the staged copies live too), any other path absolute: map both forms back.
    run_prefixes = [run_root + os.sep]
    relative_root = os.path.relpath(run_root)
    if not relative_root.startswith(os.pardir):
        run_prefixes.append(relative_root + os.sep)

    def from_run(text: str) -> str:
        if not neutralized:
            return text
        for prefix in run_prefixes:
            text = text.replace(prefix, root + os.sep)
        return text

    runs = 0
    result: subprocess.CompletedProcess[str] | None = None
    while checked:
        config = write_config(base_config, skip_modules, workdir)
        result = run_mypy(
            [f"{CONFIG_FLAG}={config}", *mypy_args, "--python-version", version, *(to_run(p) for p in checked)],
            env,
        )
        runs += 1
        stdout = from_run(result.stdout)
        if result.returncode != EXIT_BLOCKED or result.stderr.strip() or runs >= MAX_RUNS:
            break
        blamed = blocking_syntax_errors(stdout, root)
        if not blamed:
            break
        progress = False
        for path, item in sorted(blamed.items()):
            listed = next((p for p in checked if os.path.abspath(p) == path), None)
            if listed is not None:
                checked.remove(listed)
                unparseable.append(
                    {"path": listed, "reason": str(item.get("message") or ""), "line": item.get("line")},
                )
                progress = True
            module = module_name(path, root)
            if module and module not in skip_modules:
                skip_modules.add(module)
                progress = True
        if not progress:
            break

    payload = {
        "python": sys.version.split()[0],
        "target": version,
        "checked": checked,
        "unparseable": unparseable,
        "skipped_imports": sorted(skip_modules),
        "inline_config_neutralized": neutralized,
        "runs": runs,
    }
    Path(report).write_text(json.dumps(payload, indent=1) + "\n")
    if not checked or result is None:
        return 0
    sys.stdout.write(from_run(result.stdout))
    sys.stderr.write(from_run(result.stderr))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv))

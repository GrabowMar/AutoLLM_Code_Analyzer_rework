"""Task reference apps (``<slug>/reference/<stack>/``, spec 8.5 / 11.1) are evaluated data, never test modules."""
# ruff: noqa: INP001 - pytest hook file in a data directory, not a package

collect_ignore_glob = ["*/reference/*"]

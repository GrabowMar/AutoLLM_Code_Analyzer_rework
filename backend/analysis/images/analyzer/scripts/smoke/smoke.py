# ruff: noqa: S602, INP001 - deliberately insecure smoke file: every vendored semgrep rule pack must load and match
import subprocess


def run(cmd):
    return subprocess.call(cmd, shell=True)

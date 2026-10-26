"""Download a release binary and refuse it unless its sha256 matches the pin.

Usage: fetch_binary.py <url> <sha256> <dest> [<member inside a .tar.gz>]

The checksum is of the downloaded file (the tarball when a member is given),
as published next to the release; the extracted binary is made executable.
"""

# ruff: noqa: INP001, T201 - build/runtime script of the analyzer image, not an importable module

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import urllib.request
from pathlib import Path


def main(argv: list[str]) -> int:
    url, expected, dest = argv[1], argv[2].lower(), Path(argv[3])
    member = argv[4] if len(argv) > 4 else ""
    with urllib.request.urlopen(url, timeout=300) as resp:  # noqa: S310 - pinned https URL
        blob = resp.read()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        print(f"sha256 mismatch for {url}: expected {expected}, got {actual}", file=sys.stderr)
        return 1
    if member:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
            extracted = tar.extractfile(member)
            if extracted is None:
                print(f"{member} not found in {url}", file=sys.stderr)
                return 1
            blob = extracted.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(blob)
    dest.chmod(0o755)
    print(f"{dest}: {url} sha256={actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

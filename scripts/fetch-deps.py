#!/usr/bin/env python3
"""Populate ./wheelhouse/ with every artifact in uv.lock, fetched THROUGH
DependaProxy so each package passes its supply-chain validation.

The committed uv.lock keeps its canonical https://files.pythonhosted.org/... URLs
(CI reaches PyPI directly and uses them unchanged). Locally, install with:

    uv export --frozen --all-extras --no-emit-project --no-hashes -o /tmp/reqs.txt
    uv pip install --no-index --find-links wheelhouse -r /tmp/reqs.txt
    uv pip install --no-index --find-links wheelhouse --no-deps .

(`uv sync --frozen` ignores --find-links and re-downloads from the locked URLs,
so it cannot be used offline.) Every download here is verified against the sha256
recorded in uv.lock before it is kept — that hash check is what makes fetching
from DependaProxy instead of the locked URL safe.

DependaProxy serves artifacts at
    <proxy>/files/<package-name>/<version>/<filename>
so we take the name+version+filename from each [[package]] block in the lock.

Only artifacts installable on this interpreter+platform are fetched (every sdist,
plus wheels tagged for CPython 3.12 / abi3 / pure-python on linux-x86_64).

Usage: scripts/fetch-deps.py [uv.lock] [wheelhouse]
Env:   DEPENDAPROXY_PYPI_URL (default http://dependaproxy:8080/pypi)
"""

from __future__ import annotations

import hashlib
import os
import sys
import sysconfig
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

PROXY = os.environ.get("DEPENDAPROXY_PYPI_URL", "http://dependaproxy:8080/pypi").rstrip("/")

_PY_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}"  # e.g. cp312
_ARCH = sysconfig.get_platform().split("-", 1)[-1]  # "linux-x86_64" -> "x86_64"


def _wanted_wheel(filename: str) -> bool:
    """True for wheels installable on this interpreter+platform.

    Keeps: pure-python (``*-none-any``); and linux/<arch> wheels whose Python tag
    is this CPython, ABI-agnostic (``py3`` / ``py2.py3``, e.g. ruff), or a
    forward-compatible ``abi3``. Drops other interpreters, OSes and CPU arches so
    the wheelhouse holds one platform's worth of files, not every platform's.
    """
    parts = filename[:-4].split("-")  # strip ".whl"
    if len(parts) < 3:
        return False
    py_tag, abi_tag, platform_tag = parts[-3], parts[-2], parts[-1]

    if platform_tag == "any":
        return True
    if _ARCH not in platform_tag or "linux" not in platform_tag:
        return False

    py_tags = set(py_tag.split("."))
    if py_tags & {_PY_TAG, "py3", "py2"}:
        return True
    return abi_tag == "abi3"


def artifacts(lock: dict) -> list[tuple[str, str, str, str]]:
    """(name, version, filename, sha256) for every installable sdist and wheel in the lock."""
    out: list[tuple[str, str, str, str]] = []
    for pkg in lock.get("package", []):
        name, version = pkg.get("name"), pkg.get("version")
        if not name or not version:
            continue
        entries = []
        if isinstance(pkg.get("sdist"), dict):
            entries.append(pkg["sdist"])
        entries.extend(pkg.get("wheels", []) or [])
        for entry in entries:
            url, digest = entry.get("url", ""), entry.get("hash", "")
            if "files.pythonhosted.org" not in url or not digest.startswith("sha256:"):
                continue
            filename = url.rsplit("/", 1)[-1]
            if filename.endswith(".whl") and not _wanted_wheel(filename):
                continue
            out.append((name, version, filename, digest.split(":", 1)[1]))
    return out


def fetch(name: str, version: str, filename: str, sha256: str, dest: Path) -> str:
    target = dest / filename
    if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == sha256:
        return "cached"
    url = f"{PROXY}/files/{name}/{version}/{filename}"
    with urllib.request.urlopen(url, timeout=120) as response:
        body = response.read()
    got = hashlib.sha256(body).hexdigest()
    if got != sha256:
        raise ValueError(f"{filename}: sha256 mismatch (lock {sha256[:12]}…, got {got[:12]}…)")
    target.write_bytes(body)
    return "ok"


def main() -> int:
    lock_path = Path(sys.argv[1] if len(sys.argv) > 1 else "uv.lock")
    dest = Path(sys.argv[2] if len(sys.argv) > 2 else "wheelhouse")
    dest.mkdir(parents=True, exist_ok=True)

    items = artifacts(tomllib.loads(lock_path.read_text()))
    print(f"==> {len(items)} artifacts from {lock_path} via {PROXY}")

    failures: list[str] = []
    for name, version, filename, sha256 in items:
        try:
            status = fetch(name, version, filename, sha256, dest)
            print(f"    {status:>6}  {filename}")
        except (urllib.error.URLError, ValueError, TimeoutError) as exc:
            print(f"    FAILED  {filename}: {exc}", file=sys.stderr)
            failures.append(filename)

    if failures:
        print(f"==> {len(failures)} artifact(s) failed", file=sys.stderr)
        return 1
    print(f"==> wheelhouse ready: {len(list(dest.iterdir()))} files in {dest}/")
    print("    install: see 'Offline install via DependaProxy' in CLAUDE.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

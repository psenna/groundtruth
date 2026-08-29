"""Unit tests for scripts/fetch-deps.py (the DependaProxy wheelhouse fetcher)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "fetch-deps.py"

_spec = importlib.util.spec_from_file_location("fetch_deps", _SCRIPT)
assert _spec and _spec.loader
fetch_deps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fetch_deps)


def _whl(py: str, abi: str, plat: str) -> str:
    """Compose a wheel filename from its tag triple."""
    return f"demo-1.0-{py}-{abi}-{plat}.whl"


@pytest.fixture(autouse=True)
def _pin_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    # Assert against a fixed target regardless of the host running the tests.
    monkeypatch.setattr(fetch_deps, "_ARCH", "x86_64")
    monkeypatch.setattr(fetch_deps, "_PY_TAG", "cp312")


@pytest.mark.parametrize(
    ("py", "abi", "plat", "wanted"),
    [
        ("py3", "none", "any", True),  # pure python
        ("py2.py3", "none", "any", True),
        ("cp312", "cp312", "musllinux_1_2_x86_64", True),  # this cpython
        ("cp312", "cp312", "manylinux2014_x86_64", True),
        ("py3", "none", "manylinux2014_x86_64", True),  # abi-agnostic native (ruff-style)
        ("cp39", "abi3", "musllinux_1_2_x86_64", True),  # forward-compatible abi3
        ("cp313", "cp313", "manylinux2014_x86_64", False),  # other interpreter
        ("cp312", "cp312", "manylinux2014_aarch64", False),  # other arch
        ("py3", "none", "win32", False),  # other OS
        ("py3", "none", "macosx_11_0_arm64", False),
        ("cp39", "abi3", "manylinux2014_aarch64", False),  # abi3 but wrong arch
    ],
)
def test_wanted_wheel(py: str, abi: str, plat: str, wanted: bool) -> None:
    assert fetch_deps._wanted_wheel(_whl(py, abi, plat)) is wanted


def test_artifacts_skips_off_platform_wheels_but_keeps_every_sdist() -> None:
    base = "https://files.pythonhosted.org/packages/xx"
    lock = {
        "package": [
            {
                "name": "demo",
                "version": "1.0",
                "sdist": {"url": f"{base}/demo-1.0.tar.gz", "hash": "sha256:" + "a" * 64},
                "wheels": [
                    {"url": f"{base}/{_whl('py3', 'none', 'any')}", "hash": "sha256:" + "b" * 64},
                    {
                        "url": f"{base}/{_whl('cp312', 'cp312', 'win_amd64')}",
                        "hash": "sha256:" + "c" * 64,
                    },
                ],
            }
        ]
    }
    got = {row[2] for row in fetch_deps.artifacts(lock)}
    assert got == {"demo-1.0.tar.gz", _whl("py3", "none", "any")}


def test_artifacts_ignores_non_pythonhosted_or_unhashed_entries() -> None:
    lock = {
        "package": [
            {
                "name": "vcs-dep",
                "version": "0.1",
                "sdist": {"url": "git+https://example.com/x.git", "hash": ""},
            }
        ]
    }
    assert fetch_deps.artifacts(lock) == []

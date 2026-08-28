import tomllib
from pathlib import Path

import groundtruth

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_package_imports() -> None:
    assert groundtruth is not None


def test_version() -> None:
    pyproject = tomllib.loads(_PYPROJECT.read_text())
    assert groundtruth.__version__ == pyproject["project"]["version"]

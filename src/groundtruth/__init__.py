from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("groundtruth")
except PackageNotFoundError:  # pragma: no cover - package always installed in dev/CI
    __version__ = "0.0.0"

__all__ = ["__version__"]

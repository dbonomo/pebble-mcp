"""pebble-mcp: an MCP server for the Pebble smartwatch ecosystem."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

# Single source of truth is pyproject.toml's [project].version, read back out of
# the installed distribution metadata so the package, `capabilities()` and the
# wheel can never drift. The literal is only a fallback for an uninstalled
# source tree (e.g. `python -c` from a checkout with no editable install); keep
# it in step with pyproject when bumping.
try:
    __version__ = _installed_version("pebble-mcp")
except PackageNotFoundError:  # pragma: no cover - source tree without install
    __version__ = "0.2.0"

__all__ = ["__version__"]

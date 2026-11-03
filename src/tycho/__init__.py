"""Tycho: an unofficial voice co-pilot for Star Citizen."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("tycho-ai")
    """From pyproject.toml, the only place the version is written."""
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

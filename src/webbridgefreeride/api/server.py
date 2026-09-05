"""Compatibility import: both entrypoints serve the same real application."""
from ..main import app

__all__ = ["app"]

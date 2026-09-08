"""Opt-in live compatibility suite; never imported by the default test run."""

from .cases import CASES, GROUPS
from .runner import run_suite

__all__ = ["CASES", "GROUPS", "run_suite"]

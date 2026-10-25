"""Compatibility alias for the relocated REUSE linker.

Canonical implementation: scripts.incremental.incremental_reuse_linker
Legacy imports remain supported.
"""
import importlib as _importlib
import sys as _sys

_sys.modules[__name__] = _importlib.import_module(
    "scripts.incremental.incremental_reuse_linker"
)

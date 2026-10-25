"""Compatibility alias for the relocated Writer Core.

Canonical implementation: scripts.incremental.incremental_writer_core
"""
import importlib as _importlib
import sys as _sys

_sys.modules[__name__] = _importlib.import_module(
    "scripts.incremental.incremental_writer_core"
)

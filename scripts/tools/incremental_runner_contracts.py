"""Legacy import compatibility alias; canonical module at scripts.incremental.incremental_runner_contracts."""
import importlib as _importlib
import sys as _sys
_sys.modules[__name__] = _importlib.import_module("scripts.incremental.incremental_runner_contracts")

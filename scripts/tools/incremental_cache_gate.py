"""Legacy import and CLI compatibility for scripts.incremental.incremental_cache_gate."""
import importlib as _importlib
import runpy as _runpy
import sys as _sys
from pathlib import Path as _Path

_CANONICAL = "scripts.incremental.incremental_cache_gate"
if __name__ == "__main__":
    # Preserve direct execution from arbitrary cwd, including historical path.
    _root = str(_Path(__file__).resolve().parents[2])
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    _runpy.run_module(_CANONICAL, run_name="__main__", alter_sys=True)
else:
    _sys.modules[__name__] = _importlib.import_module(_CANONICAL)

"""CLI/import compatibility for scripts.incremental.promote_csv_candidate_archives."""
import importlib as _importlib
import runpy as _runpy
import sys as _sys
from pathlib import Path as _Path

_CANONICAL = 'scripts.incremental.promote_csv_candidate_archives'
if __name__ == '__main__':
    _root = str(_Path(__file__).resolve().parents[2])
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    _runpy.run_module(_CANONICAL, run_name='__main__', alter_sys=True)
else:
    _sys.modules[__name__] = _importlib.import_module(_CANONICAL)

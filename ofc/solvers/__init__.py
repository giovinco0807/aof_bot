"""Solver implementations.

Importing this package registers every solver it ships with, so
``ofc.solver.available()`` lists them without the caller naming each module.

To add your own, drop a module in here that calls ``ofc.solver.register`` at
import time and add it to ``_BUILTIN`` below. A module that fails to import —
a missing optional dependency, say — is skipped with a note rather than
taking the whole package down with it.
"""

import importlib
from typing import Dict, List

_BUILTIN = ("baseline",)

#: Modules that failed to import, mapped to why. Shown by the GUI and CLI so
#: a solver that quietly did not load is visible instead of just absent.
failed: Dict[str, str] = {}


def load_builtin() -> List[str]:
    """Import the bundled solver modules. Returns the ones that loaded."""
    loaded: List[str] = []
    for name in _BUILTIN:
        try:
            importlib.import_module(f"{__name__}.{name}")
            loaded.append(name)
        except Exception as exc:                   # noqa: BLE001 - recorded, not hidden
            failed[name] = f"{type(exc).__name__}: {exc}"
    return loaded


load_builtin()

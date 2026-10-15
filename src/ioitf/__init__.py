"""Compatibility imports for the former ioitf package.

Every alias refers to its EquiLane module, so exceptions, registries and model
caches retain their identity when old and new case packs are mixed.
"""

import importlib as _importlib
import pkgutil as _pkgutil
import sys as _sys

import equilane as _equilane
from equilane import EquiLaneError, IOITFError, ValidationError, __version__

__all__ = ["EquiLaneError", "IOITFError", "ValidationError"]

for _info in _pkgutil.iter_modules(_equilane.__path__):
    if _info.name in {"__main__", "quality_runner"}:
        continue
    _module = _importlib.import_module(f"equilane.{_info.name}")
    _sys.modules[f"ioitf.{_info.name}"] = _module
    globals()[_info.name] = _module

"""Importing this package registers every model in it.

Any module dropped into models/ is imported automatically, so a new model only
needs the @register_model("name") decorator - no edits here.
"""

import importlib
import pkgutil

from .registry import available_models, get_model, register_model

for _module in pkgutil.iter_modules(__path__):
    if _module.name != "registry":
        importlib.import_module(f"{__name__}.{_module.name}")

__all__ = ["register_model", "get_model", "available_models"]

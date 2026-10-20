from __future__ import annotations

from typing import Any, Callable

from torch import nn

_MODELS: dict[str, type[nn.Module]] = {}


def register_model(name: str) -> Callable[[type[nn.Module]], type[nn.Module]]:
    def deco(cls: type[nn.Module]) -> type[nn.Module]:
        if name in _MODELS and _MODELS[name] is not cls:
            raise KeyError(f"Model name '{name}' is already registered by {_MODELS[name].__name__}")
        _MODELS[name] = cls
        return cls
    return deco


def get_model(name: str, **kwargs: Any) -> nn.Module:
    if name not in _MODELS:
        raise KeyError(f"Unknown model '{name}'. Available: {list(_MODELS)}")
    return _MODELS[name](**kwargs)


def available_models() -> list[str]:
    return sorted(_MODELS)

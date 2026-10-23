"""YAML config -> typed, strict dataclasses.

Strict means: every field must be present in the YAML and unknown keys are an
error, so a typo can never silently fall back to a default.
"""

from __future__ import annotations

import copy
import dataclasses
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DataConfig:
    clips_dir: Path
    csv_path: Path
    classes: list[str] | None
    sample_rate: int
    clip_duration_sec: float
    val_split: float
    test_split: float
    split_by: str
    seed: int
    num_workers: int

    @property
    def num_samples(self) -> int:
        return int(round(self.sample_rate * self.clip_duration_sec))


@dataclass
class FeaturesConfig:
    type: str
    n_mels: int
    n_fft: int
    hop_length: int
    win_length: int
    n_mfcc: int
    log_eps: float
    normalize: bool
    norm_eps: float


@dataclass
class SpecAugmentConfig:
    enabled: bool
    time_mask_param: int
    freq_mask_param: int
    n_time_masks: int
    n_freq_masks: int


@dataclass
class AugmentConfig:
    enabled: bool
    time_shift_ms: float
    noise_mix_prob: float
    noise_snr_db: list[float]
    volume_jitter: float
    specaugment: SpecAugmentConfig


@dataclass
class ModelConfig:
    name: str
    args: dict[str, Any]


@dataclass
class TrainingConfig:
    batch_size: int
    epochs: int
    lr: float
    weight_decay: float
    optimizer: str
    momentum: float
    scheduler: str
    step_size: int
    step_gamma: float
    warmup_epochs: int
    grad_clip: float
    early_stopping_patience: int
    class_weighting: bool
    mixed_precision: bool
    deterministic: bool


@dataclass
class LoggingConfig:
    run_name: str
    save_dir: Path
    log_every_batches: int
    tensorboard: bool
    save_best_only: bool


@dataclass
class Config:
    data: DataConfig
    features: FeaturesConfig
    augment: AugmentConfig
    model: ModelConfig
    training: TrainingConfig
    logging: LoggingConfig
    raw: dict[str, Any] = dataclasses.field(repr=False)  # as loaded (+ overrides), for snapshots


def _build(cls: type, values: Any, where: str, base_dir: Path) -> Any:
    if not isinstance(values, dict):
        raise ValueError(f"Config section '{where}' must be a mapping")
    hints = typing.get_type_hints(cls)
    fields = [f.name for f in dataclasses.fields(cls) if f.name != "raw"]
    missing = [f for f in fields if f not in values]
    unknown = [k for k in values if k not in fields]
    if missing or unknown:
        raise ValueError(f"Config section '{where}': missing keys {missing}, unknown keys {unknown}")

    kwargs = {}
    for name in fields:
        hint, value = hints[name], values[name]
        if dataclasses.is_dataclass(hint):
            kwargs[name] = _build(hint, value, f"{where}.{name}", base_dir)
        elif hint is Path:
            path = Path(value)
            kwargs[name] = path if path.is_absolute() else (base_dir / path).resolve()
        else:
            kwargs[name] = value
    return cls(**kwargs)


def apply_overrides(raw: dict[str, Any], overrides: list[str]) -> dict[str, Any]:
    """Apply `section.key=value` overrides (value parsed as YAML) to a copy of `raw`."""
    raw = copy.deepcopy(raw)
    for item in overrides:
        if "=" not in item:
            raise ValueError(f"Override '{item}' must look like section.key=value")
        dotted, value = item.split("=", 1)
        *parents, leaf = dotted.split(".")
        node = raw
        for part in parents:
            if not isinstance(node.get(part), dict):
                raise KeyError(f"Override '{dotted}': '{part}' is not a config section")
            node = node[part]
        if leaf not in node:
            raise KeyError(f"Override '{dotted}': unknown key '{leaf}'")
        node[leaf] = yaml.safe_load(value)
    return raw


def config_from_dict(raw: dict[str, Any], base_dir: Path) -> Config:
    """Build a Config; relative paths are resolved against `base_dir` (the trainer folder)."""
    fields = [f.name for f in dataclasses.fields(Config) if f.name != "raw"]
    missing = [f for f in fields if f not in raw]
    unknown = [k for k in raw if k not in fields]
    if missing or unknown:
        raise ValueError(f"Config: missing sections {missing}, unknown sections {unknown}")
    hints = typing.get_type_hints(Config)
    sections = {name: _build(hints[name], raw[name], name, base_dir) for name in fields}

    # Model args are free-form; resolve any relative `*_path` string the same way as other paths.
    args = dict(sections["model"].args or {})  # `args:` with only comments is null in YAML
    for key, value in args.items():
        if key.endswith("_path") and isinstance(value, str) and not Path(value).is_absolute():
            args[key] = str((base_dir / value).resolve())
    sections["model"].args = args
    return Config(**sections, raw=copy.deepcopy(raw))


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge mappings. `args` (model constructor kwargs) is replaced as a whole,
    because merging kwargs of two different models would mix incompatible arguments."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict) and key != "args":
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def read_raw_config(path: Path) -> dict[str, Any]:
    """Read YAML; a top-level `base: other.yaml` (relative to this file) is loaded first
    and this file's values are merged on top. Chains of bases are allowed."""
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    base = raw.pop("base", None)
    if base is None:
        return raw
    return _merge(read_raw_config(path.parent / base), raw)


def load_config(path: Path, base_dir: Path, overrides: list[str] | None = None) -> Config:
    return config_from_dict(apply_overrides(read_raw_config(path), overrides or []), base_dir)


def save_config(cfg: Config, path: Path) -> None:
    with Path(path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.raw, f, sort_keys=False, allow_unicode=True)

"""Trainer configuration - this file is the main config.

The values are the defaults of the dataclasses below: edit them here and
train.py uses them. One-off changes without editing the file:

    python train.py -o training.epochs=2 -o features.type=mfcc

Relative paths are resolved against the trainer/ folder. Each run saves a copy
of the settings it used to runs/<run>/config.yaml.
"""

from __future__ import annotations

import copy
import dataclasses
import typing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class DataConfig:
    clips_dir: Path = Path("../Data/recordings/clips")   # collector output: clips/<label>/<clip_id>.wav
    csv_path: Path = Path("../Data/recordings/labels.csv")
    # None = all 12 labels; a subset gives a smaller output layer, other labels' clips are ignored
    classes: list[str] | None = None
    # a subset instead: field(default_factory=lambda: ["kone", "punane", "sisse", "taust", "valja"])
    sample_rate: int = 16000
    clip_duration_sec: float = 1.0          # shorter clips are zero-padded, longer ones center-cropped
    val_split: float = 0.15
    test_split: float = 0.10
    # clip | overlap | recording - what must stay together when splitting:
    #   overlap: clips whose audio overlaps (no duplicate leak)
    #   recording: whole recordings (honest 'new session' test, needs >= 3 recordings per class)
    split_by: str = "overlap"
    seed: int = 42
    num_workers: int = 4

    @property
    def num_samples(self) -> int:
        return int(round(self.sample_rate * self.clip_duration_sec))


@dataclass
class FeaturesConfig:
    type: str = "melspec"       # melspec | mfcc
    n_mels: int = 40
    n_fft: int = 400            # 25 ms at 16 kHz
    hop_length: int = 160       # 10 ms at 16 kHz -> 101 frames per 1 s clip
    win_length: int = 400
    n_mfcc: int = 13            # only used when type == mfcc
    log_eps: float = 1.0e-6     # log(mel + eps) for melspec
    normalize: bool = True      # per-feature standardization (mean/std over the training set)
    norm_eps: float = 1.0e-5    # lower bound for std


@dataclass
class SpecAugmentConfig:
    enabled: bool = True
    time_mask_param: int = 20
    freq_mask_param: int = 10
    n_time_masks: int = 1
    n_freq_masks: int = 1


@dataclass
class AugmentConfig:
    enabled: bool = True
    time_shift_ms: float = 100          # random shift within +-100 ms
    noise_mix_prob: float = 0.1         # probability of mixing a `taust` clip in
    noise_snr_db: list[float] = field(default_factory=lambda: [5, 20])   # random SNR range
    volume_jitter: float = 0.2          # random gain +-20 %
    specaugment: SpecAugmentConfig = field(default_factory=SpecAugmentConfig)


@dataclass
class ModelConfig:
    name: str = "my_model"      # resolves to MyModel via @register_model("my_model")
    # Keyword arguments for MyModel(...) - the names must match the parameters of MyModel.__init__
    # (with **kwargs, unknown ones are silently ignored). num_classes is added automatically.
    args: dict[str, Any] = field(default_factory=lambda: {
          # filters per conv block (one block = conv -> BN -> ReLU -> pool)
        "kernel_size": 3,           # conv kernel size
        "dropout": 0.2,             # before the final linear layer; 0.2 worked best on this data so far
    })


@dataclass
class TrainingConfig:
    batch_size: int = 16
    epochs: int = 100                   # early stopping usually ends the run well before this
    lr: float = 1.0e-3
    weight_decay: float = 1.0e-4
    optimizer: str = "adamw"            # adamw | adam | sgd
    momentum: float = 0.9               # sgd only
    scheduler: str = "cosine"           # cosine | step | none
    step_size: int = 10                 # step scheduler: epochs between LR drops
    step_gamma: float = 0.5             # step scheduler: LR multiplier per drop
    warmup_epochs: int = 0
    grad_clip: float = 1.0              # max grad norm; 0 disables
    early_stopping_patience: int = 50   # val set is small (~110 clips) and noisy - stops too early otherwise
    class_weighting: bool = True        # inverse-frequency weighting for imbalanced classes
    mixed_precision: bool = True        # torch.amp on CUDA (ignored on CPU)
    deterministic: bool = False         # True = reproducible cuDNN kernels (slower)


@dataclass
class LoggingConfig:
    run_name: str = "my_model_v1"
    save_dir: Path = Path("runs")
    log_every_batches: int = 10         # batch-level loss lines in training_log.txt
    tensorboard: bool = False           # optional, requires `pip install tensorboard`
    save_best_only: bool = True         # False = also keep epoch_NNN.pt for every epoch


@dataclass
class Config:
    data: DataConfig = field(default_factory=DataConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    augment: AugmentConfig = field(default_factory=AugmentConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)  # plain-dict form, for snapshots/checkpoints


# ============================================================================ #
#  Loader (no need to edit below)
# ============================================================================ #

def _to_raw(value: Any) -> Any:
    """Dataclass -> plain dict (Paths as strings), the form stored in checkpoints and snapshots."""
    if dataclasses.is_dataclass(value):
        return {f.name: _to_raw(getattr(value, f.name)) for f in dataclasses.fields(value) if f.name != "raw"}
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {k: _to_raw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_raw(v) for v in value]
    return value


def default_raw() -> dict[str, Any]:
    """The settings defined above, as a plain dict."""
    return _to_raw(Config())


def _build(cls: type, values: Any, where: str, base_dir: Path) -> Any:
    """Strict dict -> dataclass: every field must be present and unknown keys are an error,
    so a typo in an override or an old checkpoint can never silently fall back to a default."""
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
            kwargs[name] = copy.deepcopy(value)
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
    """Build a Config from its plain-dict form (checkpoints, overrides); relative paths are
    resolved against `base_dir` (the trainer folder)."""
    fields = [f.name for f in dataclasses.fields(Config) if f.name != "raw"]
    missing = [f for f in fields if f not in raw]
    unknown = [k for k in raw if k not in fields]
    if missing or unknown:
        raise ValueError(f"Config: missing sections {missing}, unknown sections {unknown}")
    hints = typing.get_type_hints(Config)
    sections = {name: _build(hints[name], raw[name], name, base_dir) for name in fields}

    # Model args are free-form; resolve any relative `*_path` string the same way as other paths.
    args = dict(sections["model"].args or {})
    for key, value in args.items():
        if key.endswith("_path") and isinstance(value, str) and not Path(value).is_absolute():
            args[key] = str((base_dir / value).resolve())
    sections["model"].args = args
    return Config(**sections, raw=copy.deepcopy(raw))


def load_config(base_dir: Path, overrides: list[str] | None = None) -> Config:
    """The settings defined in this file, with optional `section.key=value` overrides applied."""
    return config_from_dict(apply_overrides(default_raw(), overrides or []), base_dir)


def save_config(cfg: Config, path: Path) -> None:
    """Snapshot of the settings a run used (YAML, so it is easy to read next to the results)."""
    with Path(path).open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg.raw, f, sort_keys=False, allow_unicode=True)

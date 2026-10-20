"""Training / evaluation loops and checkpoint I/O shared by train.py and eval.py."""

from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm

from features import FeatureExtractor, get_extractor
from labels import LABELS
from models import get_model
from utils.config import Config, TrainingConfig, config_from_dict
from utils.seed import seed_worker

log = logging.getLogger(__name__)


@dataclass
class EvalResult:
    loss: float
    acc: float
    preds: torch.Tensor
    targets: torch.Tensor
    probs: torch.Tensor


def make_loader(dataset: Dataset, batch_size: int, shuffle: bool, num_workers: int,
                device: torch.device, seed: int, drop_last: bool = False) -> DataLoader:
    generator = torch.Generator()
    generator.manual_seed(seed)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        pin_memory=device.type == "cuda",
        persistent_workers=num_workers > 0,
        worker_init_fn=seed_worker,
        generator=generator,
        drop_last=drop_last,
    )


def train_one_epoch(model: nn.Module, extractor: FeatureExtractor, spec_augment: nn.Module | None,
                    loader: DataLoader, criterion: nn.Module, optimizer: torch.optim.Optimizer,
                    scaler: torch.amp.GradScaler, device: torch.device, use_amp: bool,
                    grad_clip: float, log_every: int, epoch: int) -> tuple[float, float]:
    model.train()
    total_loss, correct, seen = 0.0, 0, 0
    progress = tqdm(loader, desc=f"epoch {epoch} train", leave=False, dynamic_ncols=True)
    for step, (wav, target) in enumerate(progress, start=1):
        wav = wav.to(device, non_blocking=True)
        target = target.to(device, non_blocking=True)

        # Features in fp32 (log of tiny mel energies is unstable in fp16); model under autocast.
        with torch.no_grad():
            features = extractor(wav)
            if spec_augment is not None:
                features = spec_augment(features)
        with torch.amp.autocast(device_type=device.type, enabled=use_amp):
            logits = model(features)
            loss = criterion(logits, target)

        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        if grad_clip > 0:
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
        scaler.step(optimizer)
        scaler.update()

        batch = target.shape[0]
        total_loss += loss.item() * batch
        correct += (logits.argmax(dim=1) == target).sum().item()
        seen += batch
        progress.set_postfix(loss=f"{total_loss / seen:.4f}", acc=f"{correct / seen:.3f}")
        if log_every > 0 and step % log_every == 0:
            log.debug("epoch %d batch %d/%d  loss %.4f  acc %.3f",
                      epoch, step, len(loader), total_loss / seen, correct / seen)
    return total_loss / max(seen, 1), correct / max(seen, 1)


@torch.no_grad()
def evaluate(model: nn.Module, extractor: FeatureExtractor, loader: DataLoader,
             criterion: nn.Module | None, device: torch.device, use_amp: bool,
             desc: str = "eval") -> EvalResult:
    """Targets of -1 (unlabelled) are excluded from loss/accuracy but still predicted."""
    model.eval()
    all_probs, all_targets = [], []
    total_loss, labelled = 0.0, 0
    for wav, target in tqdm(loader, desc=desc, leave=False, dynamic_ncols=True):
        wav = wav.to(device, non_blocking=True)
        target = target.to(device, non_blocking=True)
        features = extractor(wav)
        with torch.amp.autocast(device_type=device.type, enabled=use_amp):
            logits = model(features)
        logits = logits.float()
        mask = target >= 0
        if criterion is not None and mask.any():
            total_loss += criterion(logits[mask], target[mask]).item() * int(mask.sum())
            labelled += int(mask.sum())
        all_probs.append(logits.softmax(dim=1).cpu())
        all_targets.append(target.cpu())

    if not all_probs:
        empty = torch.empty(0, dtype=torch.long)
        return EvalResult(0.0, 0.0, empty, empty, torch.empty(0, 0))
    probs = torch.cat(all_probs)
    targets = torch.cat(all_targets)
    preds = probs.argmax(dim=1)
    mask = targets >= 0
    acc = (preds[mask] == targets[mask]).float().mean().item() if mask.any() else 0.0
    return EvalResult(total_loss / max(labelled, 1), acc, preds, targets, probs)


# ------------------------------------------------------------------ optimisation

def build_optimizer(model: nn.Module, cfg: TrainingConfig) -> torch.optim.Optimizer:
    """Only parameters with requires_grad (a frozen backbone is left out entirely)."""
    params = [p for p in model.parameters() if p.requires_grad]
    if not params:
        raise ValueError("Model has no trainable parameters")
    if cfg.optimizer == "adamw":
        return torch.optim.AdamW(params, lr=cfg.lr, weight_decay=cfg.weight_decay)
    if cfg.optimizer == "adam":
        return torch.optim.Adam(params, lr=cfg.lr, weight_decay=cfg.weight_decay)
    if cfg.optimizer == "sgd":
        return torch.optim.SGD(params, lr=cfg.lr, momentum=cfg.momentum, weight_decay=cfg.weight_decay)
    raise ValueError(f"Unknown training.optimizer '{cfg.optimizer}' (adamw | adam | sgd)")


def build_scheduler(optimizer: torch.optim.Optimizer, cfg: TrainingConfig) -> torch.optim.lr_scheduler.LambdaLR:
    """Per-epoch LR factor: linear warmup, then cosine / step / constant."""
    warmup = max(cfg.warmup_epochs, 0)
    if cfg.scheduler not in ("cosine", "step", "none"):
        raise ValueError(f"Unknown training.scheduler '{cfg.scheduler}' (cosine | step | none)")

    def factor(epoch: int) -> float:
        if epoch < warmup:
            return (epoch + 1) / warmup
        e = epoch - warmup
        if cfg.scheduler == "cosine":
            return 0.5 * (1 + math.cos(math.pi * e / max(cfg.epochs - warmup, 1)))
        if cfg.scheduler == "step":
            return cfg.step_gamma ** (e // max(cfg.step_size, 1))
        return 1.0

    return torch.optim.lr_scheduler.LambdaLR(optimizer, factor)


def class_weights(counts: list[int], device: torch.device) -> torch.Tensor:
    """Inverse frequency, normalised so a balanced dataset gets weight 1. Absent classes get 0."""
    present = [c for c in counts if c > 0]
    total = sum(present)
    weights = [total / (len(present) * c) if c > 0 else 0.0 for c in counts]
    return torch.tensor(weights, dtype=torch.float32, device=device)


def param_summary(model: nn.Module) -> str:
    total = sum(p.numel() for p in model.parameters())
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return f"Total params: {total:,} | Trainable params: {trainable:,} (frozen: {total - trainable:,})"


# ------------------------------------------------------------------ checkpoints

def _inference_args(model_args: dict[str, Any]) -> dict[str, Any]:
    """Model kwargs for rebuilding from a checkpoint: the trained weights replace any
    pretrained init, so the pretrained file is not needed (or loaded) again."""
    args = dict(model_args)
    if "pretrained_path" in args:
        args["pretrained_path"] = None
    if "freeze_backbone" in args:
        args["freeze_backbone"] = False
    return args


def save_checkpoint(path: Path, model: nn.Module, extractor: FeatureExtractor, cfg: Config, classes: list[str],
                    epoch: int, val_acc: float, split_ids: dict[str, list[str]]) -> None:
    torch.save({
        "model_name": cfg.model.name,
        "model_args": _inference_args(cfg.model.args),
        "model_state": model.state_dict(),
        "extractor_state": extractor.state_dict(),
        "config": cfg.raw,
        "classes": classes,  # output index i -> classes[i]
        "epoch": epoch,
        "val_acc": val_acc,
        "split_ids": split_ids,
        "torch_version": torch.__version__,
    }, path)


def load_checkpoint(path: Path, device: torch.device, base_dir: Path,
                    raw_config: dict[str, Any] | None = None) -> tuple[nn.Module, FeatureExtractor, Config, dict[str, Any]]:
    """Rebuild model + extractor from a checkpoint. `raw_config` overrides the stored config
    (e.g. to point data paths elsewhere); features/model sections must stay compatible."""
    ckpt = torch.load(path, map_location=device, weights_only=False)
    raw = copy.deepcopy(raw_config or ckpt["config"])
    # Checkpoints from before data.classes existed: all 13 labels, stored under "labels".
    ckpt.setdefault("classes", ckpt.get("labels", LABELS))
    raw["data"]["classes"] = ckpt["classes"]  # the trained head defines the classes, not a swapped config
    cfg = config_from_dict(raw, base_dir)

    model = get_model(ckpt["model_name"], **_inference_args(ckpt["model_args"])).to(device)
    model.load_state_dict(ckpt["model_state"])
    extractor = get_extractor(cfg.features, cfg.data.sample_rate).to(device)
    extractor.load_state_dict(ckpt["extractor_state"])
    model.eval()
    extractor.eval()
    return model, extractor, cfg, ckpt

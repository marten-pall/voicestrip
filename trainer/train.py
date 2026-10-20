"""Train a keyword-spotting model.

    python train.py --config config.yaml
    python train.py --config config.yaml -o training.epochs=2 -o model.name=ds_cnn
"""

from __future__ import annotations

import argparse
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import torch
from torch import nn
from tqdm.contrib.logging import logging_redirect_tqdm

from data import VoiceDataset, build_spec_augment, build_waveform_augment, load_items, save_split_ids, stratified_split
from engine import (build_optimizer, build_scheduler, class_weights, evaluate, make_loader, param_summary,
                    save_checkpoint, train_one_epoch)
from features import feature_signature, get_extractor
from labels import resolve_classes
from models import available_models, get_model
from utils.config import Config, load_config, save_config
from utils.logger import MetricsLogger, setup_logging
from utils.metrics import confusion_matrix_plot, format_report
from utils.seed import set_seed

TRAINER_DIR = Path(__file__).resolve().parent
NOISE_LABEL = "taust"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("-o", "--override", action="append", default=[], metavar="KEY=VALUE",
                        help="override a config value, e.g. training.epochs=2 (repeatable)")
    return parser.parse_args()


def check_pretrained(cfg: Config) -> str | None:
    """Fail fast (before a run dir is created) if transfer learning is configured but the
    pretrained file is missing or was trained on different features. Returns a warning, if any."""
    path = cfg.model.args.get("pretrained_path")
    if not path:
        return None
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Pretrained checkpoint not found: {path}\n"
            f"See {TRAINER_DIR / 'pretrained' / 'README.md'} "
            "(or run: python scripts/pretrain_on_speech_commands.py)")
    meta = torch.load(path, map_location="cpu", weights_only=True)
    expected = meta.get("feature_signature") if isinstance(meta, dict) else None
    current = feature_signature(cfg.features, cfg.data.sample_rate, cfg.data.clip_duration_sec)
    if expected is None:
        return f"{path.name} has no feature_signature - cannot verify it was trained on the same features"
    diff = {k: (expected.get(k), current.get(k)) for k in expected.keys() | current.keys()
            if expected.get(k) != current.get(k)}
    if diff:
        raise ValueError(f"Features differ from the pretrained model's ({{key: (pretrained, config)}}): {diff}\n"
                         "Transfer learning needs identical features - fix the features/data sections.")
    return None


def make_run_dir(cfg: Config) -> Path:
    run_dir = cfg.logging.save_dir / f"{cfg.logging.run_name}_{datetime.now():%Y%m%d_%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config, TRAINER_DIR, args.override)
    classes = resolve_classes(cfg.data.classes)
    configured = cfg.model.args.get("num_classes")
    if configured is not None and configured != len(classes):
        raise ValueError(f"model.args.num_classes={configured} but data.classes has {len(classes)} classes "
                         "- remove num_classes from the config, it is derived automatically")
    cfg.model.args["num_classes"] = len(classes)
    pretrained_warning = check_pretrained(cfg)
    run_dir = make_run_dir(cfg)
    log = setup_logging(run_dir / "training_log.txt")
    save_config(cfg, run_dir / "config.yaml")

    set_seed(cfg.data.seed, cfg.training.deterministic)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    use_amp = cfg.training.mixed_precision and device.type == "cuda"
    log.info("Run dir: %s", run_dir)
    log.info("Device: %s%s | mixed precision: %s", device,
             f" ({torch.cuda.get_device_name(0)})" if device.type == "cuda" else "", use_amp)
    if pretrained_warning:
        log.warning(pretrained_warning)

    # ---- data
    log.info("Classes (%d): %s", len(classes), ", ".join(classes))
    all_items = load_items(cfg.data.csv_path, cfg.data.clips_dir)  # every known label
    items = [it for it in all_items if it.label in classes]
    ignored = Counter(it.label for it in all_items if it.label not in classes)
    if ignored:
        log.info("Ignored %d clips of labels not in data.classes: %s", sum(ignored.values()), dict(ignored))
    if not items:
        raise SystemExit(f"No usable clips found via {cfg.data.csv_path}")
    present = {it.label for it in items}
    empty = [c for c in classes if c not in present]
    if empty:
        log.warning("Classes with no clips at all (the model never learns them): %s "
                    "- record data or leave them out of data.classes", ", ".join(empty))
    splits = stratified_split(items, cfg.data.val_split, cfg.data.test_split, cfg.data.seed)
    split_ids = save_split_ids(splits, run_dir / "splits.json")
    if not splits["val"]:
        raise SystemExit("Validation split is empty - every class needs at least 3 clips for train/val/test")
    log.info("Clips: %d total -> train %d / val %d / test %d",
             len(items), len(splits["train"]), len(splits["val"]), len(splits["test"]))

    sr, num_samples = cfg.data.sample_rate, cfg.data.num_samples
    if NOISE_LABEL in classes:  # train split only, so val/test background clips never leak in
        noise_paths = [it.path for it in splits["train"] if it.label == NOISE_LABEL]
    else:  # not a class: every recorded background clip can serve as noise
        noise_paths = [it.path for it in all_items if it.label == NOISE_LABEL]
    augment = build_waveform_augment(cfg.augment, noise_paths, sr, num_samples)
    log.info("Waveform augmentation: %s", augment if augment else "off")

    train_ds = VoiceDataset(splits["train"], sr, num_samples, transform=augment, classes=classes)
    train_plain_ds = VoiceDataset(splits["train"], sr, num_samples, classes=classes)  # for normalization stats
    val_ds = VoiceDataset(splits["val"], sr, num_samples, classes=classes)
    test_ds = VoiceDataset(splits["test"], sr, num_samples, classes=classes)
    counts = train_ds.class_counts()
    log.info("Train clips per class: %s", ", ".join(f"{l}={c}" for l, c in zip(classes, counts)))

    t = cfg.training
    nw, seed = cfg.data.num_workers, cfg.data.seed
    # Drop a trailing batch of size 1: BatchNorm cannot train on a single example.
    train_loader = make_loader(train_ds, t.batch_size, True, nw, device, seed,
                               drop_last=len(train_ds) % t.batch_size == 1)
    val_loader = make_loader(val_ds, t.batch_size, False, nw, device, seed)
    test_loader = make_loader(test_ds, t.batch_size, False, nw, device, seed)

    # ---- features
    extractor = get_extractor(cfg.features, sr).to(device)
    if cfg.features.normalize:
        extractor.fit_stats(make_loader(train_plain_ds, t.batch_size, False, nw, device, seed), device)
    spec_augment = build_spec_augment(cfg.augment)
    if spec_augment is not None:
        spec_augment = spec_augment.to(device)
    sample_shape = tuple(extractor(train_plain_ds[0][0].unsqueeze(0).to(device)).shape[1:])
    log.info("Features: %s -> input shape %s | SpecAugment: %s",
             cfg.features.type, sample_shape, "on" if spec_augment is not None else "off")

    # ---- model / optimisation
    model = get_model(cfg.model.name, **cfg.model.args).to(device)
    log.info("Model: %s %s | registered: %s", cfg.model.name, cfg.model.args, ", ".join(available_models()))
    log.info(param_summary(model))

    weights = class_weights(counts, device) if t.class_weighting else None
    criterion = nn.CrossEntropyLoss(weight=weights)
    eval_criterion = nn.CrossEntropyLoss()  # unweighted, comparable across runs
    optimizer = build_optimizer(model, t)
    scheduler = build_scheduler(optimizer, t)
    scaler = torch.amp.GradScaler(device.type, enabled=use_amp)
    metrics_log = MetricsLogger(run_dir, cfg.logging.tensorboard)

    # ---- training loop
    best_acc, best_epoch, stale = -1.0, 0, 0
    with logging_redirect_tqdm():
        for epoch in range(1, t.epochs + 1):
            start = time.time()
            lr = optimizer.param_groups[0]["lr"]
            train_loss, train_acc = train_one_epoch(
                model, extractor, spec_augment, train_loader, criterion, optimizer, scaler,
                device, use_amp, t.grad_clip, cfg.logging.log_every_batches, epoch)
            val = evaluate(model, extractor, val_loader, eval_criterion, device, use_amp, desc=f"epoch {epoch} val")
            scheduler.step()

            metrics_log.log_epoch({"epoch": epoch, "train_loss": train_loss, "train_acc": train_acc,
                                   "val_loss": val.loss, "val_acc": val.acc, "lr": lr})
            improved = val.acc > best_acc
            log.info("epoch %3d/%d  train loss %.4f acc %5.1f%% | val loss %.4f acc %5.1f%% | lr %.2e | %.1fs%s",
                     epoch, t.epochs, train_loss, train_acc * 100, val.loss, val.acc * 100, lr,
                     time.time() - start, "  * best" if improved else "")

            if improved:
                best_acc, best_epoch, stale = val.acc, epoch, 0
                save_checkpoint(run_dir / "best.pt", model, extractor, cfg, classes, epoch, val.acc, split_ids)
            else:
                stale += 1
            save_checkpoint(run_dir / "last.pt", model, extractor, cfg, classes, epoch, val.acc, split_ids)
            if not cfg.logging.save_best_only:
                save_checkpoint(run_dir / f"epoch_{epoch:03d}.pt", model, extractor, cfg, classes, epoch, val.acc, split_ids)

            if 0 < t.early_stopping_patience <= stale:
                log.info("Early stopping: no val improvement for %d epochs", stale)
                break
    metrics_log.close()
    log.info("Best val acc %.1f%% at epoch %d", best_acc * 100, best_epoch)

    # ---- final test evaluation with the best checkpoint
    best = torch.load(run_dir / "best.pt", map_location=device, weights_only=False)
    model.load_state_dict(best["model_state"])
    if not splits["test"]:
        log.warning("Test split is empty - skipping final evaluation")
        return
    test = evaluate(model, extractor, test_loader, eval_criterion, device, use_amp, desc="test")
    cm_path = confusion_matrix_plot(test.preds, test.targets, classes, run_dir / "confusion_matrix.png",
                                    title=f"{cfg.logging.run_name} test")
    for line in format_report(test.preds, test.targets, classes, f"Final test results (best epoch {best_epoch})"):
        log.info(line)
    log.info("Confusion matrix saved: %s", cm_path)


if __name__ == "__main__":
    main()

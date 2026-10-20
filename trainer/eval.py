"""Evaluate a checkpoint without retraining.

    python eval.py --checkpoint runs/<run>/best.pt                 # saved test split
    python eval.py --checkpoint runs/<run>/best.pt --split all     # every clip in the CSV
    python eval.py --checkpoint runs/<run>/best.pt --wav-dir some/folder

--wav-dir: every *.wav below the folder is predicted. If a WAV sits in a folder
named after a label (e.g. some/folder/punane/x.wav) it counts as ground truth
and metrics are reported; otherwise only predictions are listed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import torch
from torch import nn

from data import ClipItem, VoiceDataset, load_items, select_by_ids
from engine import evaluate, load_checkpoint, make_loader
from utils.config import read_raw_config
from utils.logger import setup_logging
from utils.metrics import confusion_matrix_plot, format_report

TRAINER_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--split", choices=["test", "val", "train", "all"], default="test",
                        help="which clips of the CSV to evaluate (ignored with --wav-dir)")
    parser.add_argument("--wav-dir", type=Path, help="evaluate an arbitrary folder of WAVs instead")
    parser.add_argument("--config", type=Path,
                        help="use this config's data paths instead of the ones stored in the checkpoint")
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    return parser.parse_args()


def wav_dir_items(folder: Path, classes: list[str]) -> list[ClipItem]:
    """Folder named after one of the model's classes = ground truth; anything else is unlabelled."""
    items = []
    for path in sorted(folder.rglob("*.wav")):
        label = path.parent.name if path.parent.name in classes else None
        items.append(ClipItem(path.stem, path, label, "", "wav-dir"))
    return items


def main() -> None:
    args = parse_args()
    log = setup_logging()
    device = torch.device(args.device)

    raw_config = None
    if args.config:
        ckpt_cfg = torch.load(args.checkpoint, map_location="cpu", weights_only=False)["config"]
        ckpt_cfg["data"] = read_raw_config(args.config)["data"]  # only the data section is swapped
        raw_config = ckpt_cfg
    model, extractor, cfg, ckpt = load_checkpoint(args.checkpoint, device, TRAINER_DIR, raw_config)
    classes = ckpt["classes"]
    log.info("Checkpoint: %s (model %s, epoch %d, val acc %.1f%%)",
             args.checkpoint, ckpt["model_name"], ckpt["epoch"], ckpt["val_acc"] * 100)
    log.info("Classes (%d): %s", len(classes), ", ".join(classes))

    if args.wav_dir:
        items = wav_dir_items(args.wav_dir, classes)
        source = str(args.wav_dir)
    else:
        all_items = load_items(cfg.data.csv_path, cfg.data.clips_dir, classes)
        if args.split == "all":
            items = all_items
        else:
            items = select_by_ids(all_items, ckpt["split_ids"][args.split])
        source = f"{args.split} split"
    if not items:
        raise SystemExit(f"No clips to evaluate ({source})")

    dataset = VoiceDataset(items, cfg.data.sample_rate, cfg.data.num_samples, classes=classes)
    loader = make_loader(dataset, args.batch_size, False, args.num_workers, device, cfg.data.seed)
    use_amp = cfg.training.mixed_precision and device.type == "cuda"
    result = evaluate(model, extractor, loader, nn.CrossEntropyLoss(), device, use_amp, desc=source)

    labelled = result.targets >= 0
    if not labelled.all():
        for item, pred, probs in zip(items, result.preds, result.probs):
            if item.label is None:
                log.info("  %-40s -> %-9s (p=%.2f)", item.path.name, classes[pred], probs[pred].item())
    if labelled.any():
        preds, targets = result.preds[labelled], result.targets[labelled]
        for line in format_report(preds, targets, classes, f"Results on {source} ({int(labelled.sum())} clips)"):
            log.info(line)
        name = "wavdir" if args.wav_dir else args.split
        cm_path = confusion_matrix_plot(preds, targets, classes,
                                        args.checkpoint.parent / f"eval_{name}_confusion_matrix.png",
                                        title=f"{args.checkpoint.parent.name} / {source}")
        log.info("Confusion matrix saved: %s", cm_path)


if __name__ == "__main__":
    main()

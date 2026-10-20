from __future__ import annotations

import json
import logging
import random
from collections import defaultdict
from pathlib import Path

from .dataset import ClipItem

log = logging.getLogger(__name__)

SPLITS = ("train", "val", "test")


def stratified_split(items: list[ClipItem], val_frac: float, test_frac: float,
                     seed: int) -> dict[str, list[ClipItem]]:
    """Per-label shuffle + split, so every split keeps the label distribution.

    Classes with fewer than 3 clips cannot be split three ways and go to train
    only; classes with >= 3 clips get at least one val and one test clip.
    Deterministic for a given CSV content + seed (independent of global RNG state).
    """
    by_label: dict[str, list[ClipItem]] = defaultdict(list)
    for item in items:
        by_label[item.label].append(item)

    rng = random.Random(seed)
    splits: dict[str, list[ClipItem]] = {name: [] for name in SPLITS}
    too_small = []
    for label in sorted(by_label):
        group = sorted(by_label[label], key=lambda it: it.clip_id)
        rng.shuffle(group)
        n = len(group)
        if n < 3:
            too_small.append(f"{label}={n}")
            splits["train"].extend(group)
            continue
        n_test = max(1, round(n * test_frac))
        n_val = max(1, round(n * val_frac))
        if n_test + n_val >= n:  # always keep at least one training clip
            n_test, n_val = 1, 1
        splits["test"].extend(group[:n_test])
        splits["val"].extend(group[n_test:n_test + n_val])
        splits["train"].extend(group[n_test + n_val:])

    if too_small:
        log.warning("Classes with < 3 clips are train-only (no val/test): %s", ", ".join(too_small))
    return splits


def save_split_ids(splits: dict[str, list[ClipItem]], path: Path) -> dict[str, list[str]]:
    ids = {name: [it.clip_id for it in items] for name, items in splits.items()}
    Path(path).write_text(json.dumps(ids, indent=2), encoding="utf-8")
    return ids


def select_by_ids(items: list[ClipItem], clip_ids: list[str]) -> list[ClipItem]:
    """Items matching saved clip_ids (e.g. a checkpoint's test split); warns about vanished clips."""
    by_id = {it.clip_id: it for it in items}
    found = [by_id[cid] for cid in clip_ids if cid in by_id]
    if len(found) < len(clip_ids):
        log.warning("%d of %d saved clip_ids no longer exist in the CSV / on disk",
                    len(clip_ids) - len(found), len(clip_ids))
    return found

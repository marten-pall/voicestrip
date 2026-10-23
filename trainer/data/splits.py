"""Train / val / test splitting.

Clips are first grouped, then whole groups are assigned to splits, so related
clips can never end up on both sides of the train/test boundary:

- "clip":      every clip is its own group (old behaviour; overlapping boxes leak)
- "overlap":   clips of the same recording whose audio overlaps (start times closer
               than one clip length, chained) form one group
- "recording": all clips of a recording form one group ("new session" test;
               needs >= 3 recordings per class to give that class val/test data)

Groups are stratified by their majority label and assigned greedily towards
the target clip counts. Deterministic for a given CSV content + seed.
"""

from __future__ import annotations

import json
import logging
import random
from collections import Counter, defaultdict
from pathlib import Path

from .dataset import ClipItem

log = logging.getLogger(__name__)

SPLITS = ("train", "val", "test")
SPLIT_MODES = ("clip", "overlap", "recording")
EPS = 1e-6  # boxes that merely touch (e.g. 0.45 s and 1.45 s) do not overlap
Group = list[ClipItem]


def make_groups(items: list[ClipItem], split_by: str, clip_duration_sec: float) -> list[Group]:
    if split_by not in SPLIT_MODES:
        raise ValueError(f"data.split_by must be one of {SPLIT_MODES}, got '{split_by}'")
    if split_by == "clip":
        return [[it] for it in items]

    by_recording: dict[str, list[ClipItem]] = defaultdict(list)
    for item in items:
        by_recording[item.recording_id].append(item)
    if split_by == "recording":
        return list(by_recording.values())

    groups: list[Group] = []
    for clips in by_recording.values():
        clips = sorted(clips, key=lambda it: (it.start_sec, it.clip_id))
        current: Group = [clips[0]]
        reach = clips[0].start_sec + clip_duration_sec  # end of the audio covered so far
        for clip in clips[1:]:
            if clip.start_sec < reach - EPS:  # overlaps something already in the group
                current.append(clip)
            else:
                groups.append(current)
                current = [clip]
            reach = max(reach, clip.start_sec + clip_duration_sec)
        groups.append(current)
    return groups


def _majority_label(group: Group) -> str:
    counts = Counter(it.label for it in group)
    best = max(counts.values())
    return min(label for label, n in counts.items() if n == best)


def _assign(groups: list[Group], val_frac: float, test_frac: float) -> dict[str, list[Group]]:
    """Greedy: one group each to test and val first (the group whose size is closest to
    the target), then fill test/val up to their target clip counts; the rest is train."""
    n_clips = sum(len(g) for g in groups)
    targets = {"test": n_clips * test_frac, "val": n_clips * val_frac}
    out: dict[str, list[Group]] = {name: [] for name in SPLITS}
    pool = list(groups)
    for name in ("test", "val"):  # guarantee >= 1 group in each (caller ensures >= 3 groups)
        pick = min(pool, key=lambda g: abs(len(g) - targets[name]))
        pool.remove(pick)
        out[name].append(pick)
    counts = {name: sum(len(g) for g in out[name]) for name in ("test", "val")}
    for group in pool:
        for name in ("test", "val"):
            if counts[name] + len(group) / 2 <= targets[name]:
                out[name].append(group)
                counts[name] += len(group)
                break
        else:
            out["train"].append(group)
    if not out["train"]:  # tiny edge case: never leave a class without training data
        out["train"].append(out["val"].pop() if len(out["val"]) > 1 else out["test"].pop())
    return out


def stratified_split(items: list[ClipItem], val_frac: float, test_frac: float, seed: int,
                     split_by: str = "clip", clip_duration_sec: float = 1.0) -> dict[str, list[ClipItem]]:
    """Group-aware, label-stratified split. Labels with fewer than 3 groups go to train only."""
    groups = make_groups(items, split_by, clip_duration_sec)
    by_label: dict[str, list[Group]] = defaultdict(list)
    for group in groups:
        by_label[_majority_label(group)].append(group)

    rng = random.Random(seed)
    splits: dict[str, list[ClipItem]] = {name: [] for name in SPLITS}
    for label in sorted(by_label):
        label_groups = sorted(by_label[label], key=lambda g: min(it.clip_id for it in g))
        rng.shuffle(label_groups)
        if len(label_groups) < 3:  # cannot split three ways
            assigned = {"train": label_groups, "val": [], "test": []}
        else:
            assigned = _assign(label_groups, val_frac, test_frac)
        for name, assigned_groups in assigned.items():
            for group in assigned_groups:
                splits[name].extend(group)

    unit = {"clip": "clips", "overlap": "overlap groups", "recording": "recordings"}[split_by]
    log.info("Split by %s: %d clips in %d %s", split_by, len(items), len(groups), unit)
    for name in ("val", "test"):
        present = {it.label for it in splits[name]}
        absent = sorted({it.label for it in items} - present)
        if absent:
            log.warning("No %s clips for %s with split_by=%s (too few %s, or their clips sit in groups of "
                        "another majority label) - their %s metrics will be n/a", name, ", ".join(absent),
                        split_by, unit, name)
    return splits


def leakage_report(splits: dict[str, list[ClipItem]], clip_duration_sec: float) -> dict[str, dict[str, int]]:
    """For val/test: how many clips overlap a training clip's audio, and how many come
    from a recording that also has training clips (same session/room/voice)."""
    train_starts: dict[str, list[float]] = defaultdict(list)
    for it in splits["train"]:
        train_starts[it.recording_id].append(it.start_sec)
    report = {}
    for name in ("val", "test"):
        overlapping = shared = 0
        for it in splits[name]:
            starts = train_starts.get(it.recording_id, [])
            shared += bool(starts)
            overlapping += any(abs(it.start_sec - s) < clip_duration_sec - EPS for s in starts)
        report[name] = {"clips": len(splits[name]), "overlap_train": overlapping, "recording_in_train": shared}
    return report


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

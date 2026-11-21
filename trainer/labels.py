"""Class labels, read from ../labels.json (shared with the collector app: same ASCII keys
as its CSV and clip folders).

Index mapping is the alphabetical order of the keys, so it never depends on the file's
order. A trained checkpoint stores its own class list (`classes`), which is what eval and
export use.
"""

import json
from pathlib import Path

LABEL_FILE = Path(__file__).resolve().parent.parent / "labels.json"
LABELS: list[str] = sorted(json.loads(LABEL_FILE.read_text(encoding="utf-8")))

assert len(LABELS) == len(set(LABELS)) >= 2, f"{LABEL_FILE} must define at least 2 unique labels"


def resolve_classes(selected: list[str] | None) -> list[str]:
    """Classes a model is trained on: all LABELS, or the `data.classes` subset.

    The subset is sorted alphabetically, so output index i always means the same
    label for a given set of classes, whatever order the config lists them in.
    """
    if selected is None:
        return list(LABELS)
    unknown = [c for c in selected if c not in LABELS]
    if unknown:
        raise ValueError(f"data.classes contains unknown labels {unknown}. Valid: {LABELS}")
    if len(set(selected)) != len(selected):
        raise ValueError(f"data.classes has duplicates: {selected}")
    if len(selected) < 2:
        raise ValueError("data.classes needs at least 2 classes")
    return sorted(selected)

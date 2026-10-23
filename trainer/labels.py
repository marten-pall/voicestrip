"""Class labels shared with the collector app (ASCII keys, as used in its CSV and folders).

Index mapping is the alphabetical order of this list - never reorder or a trained
checkpoint's class indices stop matching. Keep in sync with `LABELS` in the collector's app.py.
"""

LABELS: list[str] = sorted([
    "hajuv",
    "heledam",
    "kone",
    "punane",
    "roheline",
    "sinine",
    "sisse",
    "taust",
    "tumedam",
    "valge",
    "valja",
    "vilkuv",
])

assert len(LABELS) == len(set(LABELS)) == 12, "LABELS must contain 12 unique keys"


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

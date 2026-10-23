"""One-off migration: merge the old `tundmatu` class into `taust`.

    venv\\Scripts\\python.exe scripts\\merge_tundmatu_to_taust.py      (Windows)
    venv/bin/python scripts/merge_tundmatu_to_taust.py                (Linux/Mac)

- moves every WAV in recordings/clips/tundmatu/ to recordings/clips/taust/,
  renaming "<recording_id>_<N>_tundmatu.wav" -> "<recording_id>_<N>_taust.wav"
- rewrites labels.csv rows: label, clip_id and clip_path (backup: labels.csv.bak)
- recordings.csv rows with default_label "tundmatu" get "taust" (backup: recordings.csv.bak)
- leaves the (then empty) clips/tundmatu/ folder in place

Stop the collector (app.py) before running. Safe to run twice: the second run changes nothing.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # collector folder -> `import app`

import app  # noqa: E402

OLD, NEW = "tundmatu", "taust"


def renamed(name: str) -> str:
    """'<id>_<N>_tundmatu' -> '<id>_<N>_taust' (only the trailing label suffix changes)."""
    suffix = f"_{OLD}"
    return name[: -len(suffix)] + f"_{NEW}" if name.endswith(suffix) else name


def backup(path: Path) -> Path | None:
    """Copy to <name>.bak; never overwrite an earlier backup (adds a timestamp instead)."""
    if not path.exists():
        return None
    target = path.with_name(path.name + ".bak")
    if target.exists():
        target = path.with_name(f"{path.name}.bak.{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(path, target)
    return target


def main() -> None:
    old_dir, new_dir = app.CLIPS_DIR / OLD, app.CLIPS_DIR / NEW
    new_dir.mkdir(parents=True, exist_ok=True)
    rows = app.read_csv(app.LABELS_CSV)
    recordings = app.read_csv(app.RECORDINGS_CSV)

    # ---- plan: every file to move (CSV-referenced clips + any orphan WAVs in the folder)
    moves: dict[Path, Path] = {}
    for row in rows:
        if row["label"] == OLD:
            src = app.safe_rec_path(row["clip_path"])
            moves[src] = new_dir / f"{renamed(row['clip_id'])}.wav"
    if old_dir.is_dir():
        for src in sorted(old_dir.glob("*.wav")):
            moves.setdefault(src.resolve(), new_dir / f"{renamed(src.stem)}.wav")

    missing = [src for src in moves if not src.exists()]
    clashes = [dst for src, dst in moves.items() if src.exists() and dst.exists()]
    if clashes:
        sys.exit(f"Aborted, nothing changed: target files already exist: {[d.name for d in clashes[:5]]}")

    # ---- move files (rolled back if anything fails)
    done: list[tuple[Path, Path]] = []
    try:
        for src, dst in moves.items():
            if src.exists():
                shutil.move(str(src), str(dst))
                done.append((src, dst))
    except Exception:
        for src, dst in reversed(done):
            shutil.move(str(dst), str(src))
        raise

    # ---- rewrite CSVs (backups first)
    updated = 0
    for row in rows:
        if row["label"] == OLD:
            row["label"] = NEW
            row["clip_id"] = renamed(row["clip_id"])
            row["clip_path"] = app.rel_path(new_dir / f"{row['clip_id']}.wav")
            updated += 1
    if updated:
        bak = backup(app.LABELS_CSV)
        app.write_csv(app.LABELS_CSV, app.CLIP_FIELDS, rows)
        print(f"Backup: {bak}")

    defaults = sum(1 for r in recordings if r["default_label"] == OLD)
    if defaults:
        for r in recordings:
            if r["default_label"] == OLD:
                r["default_label"] = NEW
        print(f"Backup: {backup(app.RECORDINGS_CSV)}")
        app.write_csv(app.RECORDINGS_CSV, app.RECORDING_FIELDS, recordings)

    print(f"Moved {len(done)} clips, updated {updated} CSV rows"
          + (f", {defaults} recording default labels" if defaults else ""))
    if missing:
        print(f"Warning: {len(missing)} CSV rows pointed to WAVs that did not exist "
              f"(rows relabelled anyway): {[m.name for m in missing[:5]]}")


if __name__ == "__main__":
    main()

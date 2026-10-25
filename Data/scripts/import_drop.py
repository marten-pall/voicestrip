"""Import a recordings/ folder copied off another collector (the Pi) into this one.

    venv\\Scripts\\python.exe scripts\\import_drop.py --from C:\\incoming\\recordings
    venv/bin/python scripts/import_drop.py --from ~/incoming/recordings --apply

DRY RUN BY DEFAULT: prints the plan and changes nothing without --apply.

Why this is not a plain copy
----------------------------
Both collectors allocate recording ids with next_recording_id(), which is
"YYYYMMDD_ + 1 + highest used TODAY" counted against their own disk. The Pi and
the laptop therefore both produce 20261025_001 on the same day, for two entirely
different recordings. Copying the folders together would have one silently
overwrite the other, and leave labels.csv with two meanings for one clip_id.

So an incoming recording is identified by its AUDIO, not by its id:

  * id free here            -> keep the id
  * id taken, same audio    -> already imported, skip it (re-running is a no-op)
  * id taken, other audio   -> give it the next free id here and rewrite
                               recording_id, clip_id, clip_path and original_path
                               to match, for the whole recording at once

Nothing in this folder is ever overwritten. Clips whose WAV did not arrive are
reported and left out, because a labels.csv row in front of a missing file is
worse than a missing row; pass --allow-missing-audio to take them anyway.

Stop the collector (app.py) on BOTH machines before copying and running.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # collector folder -> `import app`

import app  # noqa: E402

CHUNK = 1 << 20


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def backup(path: Path) -> Path | None:
    """Copy to <name>.bak; never overwrite an earlier backup (adds a timestamp)."""
    if not path.exists():
        return None
    target = path.with_name(path.name + ".bak")
    if target.exists():
        target = path.with_name(f"{path.name}.bak.{datetime.now():%Y%m%d_%H%M%S}")
    shutil.copy2(path, target)
    return target


def find_original(root: Path, recording_id: str) -> Path | None:
    matches = sorted((root / "original").glob(f"{recording_id}_*.wav"))
    return matches[0] if matches else None


def fingerprint(root: Path, recording_id: str, clips: list[dict]) -> str | None:
    """What makes this recording itself: its original WAV, or failing that its clips.

    Returns None when neither is on disk, in which case the id is all we have and
    the recording cannot be told apart from a local one that shares it.
    """
    original = find_original(root, recording_id)
    if original:
        return "orig:" + sha256_of(original)
    digests = []
    for row in sorted(clips, key=lambda r: r["clip_id"]):
        path = root / row["clip_path"]
        if path.is_file():
            digests.append(sha256_of(path))
    return ("clips:" + hashlib.sha256("".join(digests).encode()).hexdigest()
            if digests else None)


def local_ids_for_day(day: str, recordings: list[dict]) -> set[int]:
    """Counters already used locally on that day, from the CSV and from disk."""
    used = set()
    stems = [r["recording_id"] for r in recordings]
    stems += [p.stem for p in app.ORIGINAL_DIR.glob(f"{day}_*.wav")]
    for stem in stems:
        parts = stem.split("_")
        if parts[0] == day and len(parts) >= 2 and parts[1].isdigit():
            used.add(int(parts[1]))
    return used


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from", dest="src", required=True,
                    help="the recordings/ folder copied off the other machine")
    ap.add_argument("--apply", action="store_true", help="actually import")
    ap.add_argument("--allow-missing-audio", action="store_true",
                    help="also take labels.csv rows whose WAV did not arrive")
    ap.add_argument("--limit", type=int, default=12, help="examples to print per section")
    args = ap.parse_args()

    src = Path(args.src).resolve()
    if not src.is_dir():
        return fail(f"no such folder: {src}")
    if src == app.REC_DIR.resolve():
        return fail("that is this collector's own recordings/ folder")
    in_labels_csv, in_recs_csv = src / "labels.csv", src / "recordings.csv"
    if not in_labels_csv.is_file():
        return fail(f"no labels.csv in {src}")

    print(f"from  {src}")
    print(f"into  {app.REC_DIR}")
    print()

    in_clips = app.read_csv(in_labels_csv)
    in_recs = app.read_csv(in_recs_csv)
    here_clips = app.read_csv(app.LABELS_CSV)
    here_recs = app.read_csv(app.RECORDINGS_CSV)

    here_rec_by_id = {r["recording_id"]: r for r in here_recs}
    here_clip_ids = {r["clip_id"] for r in here_clips}

    # group the incoming clips by recording; a recording with no row of its own
    # still forms a group, so nothing is dropped for want of metadata
    by_rec: dict[str, list[dict]] = {}
    for row in in_clips:
        by_rec.setdefault(row["recording_id"], []).append(row)
    in_rec_by_id = {r["recording_id"]: r for r in in_recs}
    for rid in in_rec_by_id:
        by_rec.setdefault(rid, [])

    bad_label = sorted({r["clip_id"] for r in in_clips if r["label"] not in app.LABELS})
    if bad_label:
        print(f"ERROR: {len(bad_label)} clip(s) carry a label this collector does not know: "
              f"{sorted({r['label'] for r in in_clips if r['label'] not in app.LABELS})}")
        return 1

    # ---- decide what happens to each incoming recording -------------------- #
    # A recording is recognised by its AUDIO against EVERY recording already
    # here, not just the one sharing its id. A renumbered import keeps its new
    # id, so matching on id alone would import it again under another id on the
    # next run, and again on the one after that.
    local_rids = {r["recording_id"] for r in here_recs} | {r["recording_id"] for r in here_clips}
    seen_here: dict[str, str] = {}          # fingerprint -> the id it has here
    for rid in sorted(local_rids):
        fp = fingerprint(app.REC_DIR, rid, [c for c in here_clips if c["recording_id"] == rid])
        if fp:
            seen_here.setdefault(fp, rid)

    keep, rekey, already = {}, {}, {}
    ambiguous = []
    needs_id = []

    for rid in sorted(by_rec):
        incoming_fp = fingerprint(src, rid, by_rec[rid])
        if incoming_fp and incoming_fp in seen_here:
            already[rid] = seen_here[incoming_fp]
            continue
        if rid not in local_rids and not find_original(app.REC_DIR, rid):
            keep[rid] = rid
            continue
        if incoming_fp is None:
            ambiguous.append(rid)
        needs_id.append(rid)

    # Allocate only after every decision is made: a new id has to dodge both
    # what is already here and what the rest of this drop is about to keep.
    day_counters: dict[str, set[int]] = {}
    for rid in list(keep) + needs_id:
        day = rid.split("_")[0]
        if day not in day_counters:
            day_counters[day] = local_ids_for_day(day, here_recs)
    for rid in keep:
        day, _, counter = rid.partition("_")
        if counter.isdigit():
            day_counters[day].add(int(counter))

    for rid in needs_id:
        day = rid.split("_")[0]
        nxt = max(day_counters[day], default=0) + 1
        day_counters[day].add(nxt)
        rekey[rid] = f"{day}_{nxt:03d}"

    new_id = {**keep, **rekey}

    # ---- build the plan ----------------------------------------------------- #
    def renamed_clip(row: dict, rid_new: str) -> tuple[str, str]:
        """New clip_id and clip_path for a clip moving to a new recording id."""
        rest = row["clip_id"][len(row["recording_id"]):].lstrip("_")
        cid = f"{rid_new}_{rest}" if rest else f"{rid_new}_{row['label']}"
        return cid, f"clips/{row['label']}/{cid}.wav"

    copies: list[tuple[Path, Path]] = []
    clip_rows: list[dict] = []
    rec_rows: list[dict] = []
    missing_audio: list[str] = []
    clip_id_clash: list[str] = []

    for rid, rid_new in sorted(new_id.items()):
        rec = in_rec_by_id.get(rid)
        if rec:
            row = dict(rec)
            row["recording_id"] = rid_new
            original = find_original(src, rid)
            if original:
                target = app.ORIGINAL_DIR / f"{rid_new}{original.stem[len(rid):]}.wav"
                copies.append((original, target))
                row["original_path"] = app.rel_path(target)
            rec_rows.append(row)

        for clip in sorted(by_rec[rid], key=lambda r: r["clip_id"]):
            source_wav = src / clip["clip_path"]
            cid, cpath = ((clip["clip_id"], clip["clip_path"]) if rid_new == rid
                          else renamed_clip(clip, rid_new))
            if not source_wav.is_file():
                missing_audio.append(clip["clip_id"])
                if not args.allow_missing_audio:
                    continue
            if cid in here_clip_ids:
                clip_id_clash.append(cid)
                continue
            row = dict(clip)
            row["recording_id"], row["clip_id"], row["clip_path"] = rid_new, cid, cpath
            clip_rows.append(row)
            if source_wav.is_file():
                copies.append((source_wav, app.REC_DIR / cpath))

    # clip WAVs in the drop that no labels.csv row mentions
    listed = {(src / r["clip_path"]).resolve() for r in in_clips}
    orphan_wavs = sorted(p.relative_to(src).as_posix()
                         for p in (src / "clips").rglob("*.wav")
                         if p.is_file() and p.resolve() not in listed) \
        if (src / "clips").is_dir() else []

    # ---- report ------------------------------------------------------------- #
    def show(title, items, note=""):
        if not items:
            return
        print(f"{title}: {len(items):,}{note}")
        for item in items[:args.limit]:
            print(f"    {item}")
        if len(items) > args.limit:
            print(f"    ... and {len(items) - args.limit:,} more")
        print()

    print(f"incoming: {len(in_recs):,} recording(s), {len(in_clips):,} clip row(s)")
    print(f"here    : {len(here_recs):,} recording(s), {len(here_clips):,} clip row(s)")
    print()

    show("new, id is free", sorted(f"{k}" for k in keep))
    show("ALREADY IMPORTED, same audio (skipped)",
         [old if old == here else f"{old}  ->  already here as {here}"
          for old, here in sorted(already.items())])
    show("id taken by a different recording -> renumbered",
         [f"{old}  ->  {new}" for old, new in sorted(rekey.items())])
    show("could not compare audio, renumbered to be safe", ambiguous,
         "   (no original WAV on one side)")
    show("labels.csv row with no WAV in the drop", missing_audio,
         "" if args.allow_missing_audio else "   (NOT taken; --allow-missing-audio to take them)")
    show("clip WAV in the drop that labels.csv does not mention", orphan_wavs)
    show("clip_id already here after renumbering (skipped)", clip_id_clash)

    existing_targets = [str(dst.relative_to(app.REC_DIR)) for _, dst in copies if dst.exists()]
    if existing_targets:
        print(f"ERROR: {len(existing_targets)} target file(s) already exist. "
              f"Nothing was changed.")
        for t in existing_targets[:args.limit]:
            print(f"    {t}")
        return 1

    print(f"PLAN: copy {len(copies):,} file(s), "
          f"add {len(rec_rows):,} recording row(s) and {len(clip_rows):,} clip row(s)")

    if not args.apply:
        print()
        print("DRY RUN - nothing was changed. Re-run with --apply.")
        return 0

    # ---- apply (files first, rolled back if a copy fails) -------------------- #
    done: list[Path] = []
    try:
        for source_wav, target in copies:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_wav, target)
            done.append(target)
    except Exception:
        for target in reversed(done):
            target.unlink(missing_ok=True)
        raise
    print()
    print(f"copied {len(done):,} file(s)")

    if rec_rows:
        print(f"Backup: {backup(app.RECORDINGS_CSV)}")
        app.append_csv(app.RECORDINGS_CSV, app.RECORDING_FIELDS, rec_rows)
    if clip_rows:
        print(f"Backup: {backup(app.LABELS_CSV)}")
        app.append_csv(app.LABELS_CSV, app.CLIP_FIELDS, clip_rows)

    print(f"recordings.csv: +{len(rec_rows):,}   labels.csv: +{len(clip_rows):,}")
    return 0


def fail(msg: str) -> int:
    print(f"ERROR: {msg}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

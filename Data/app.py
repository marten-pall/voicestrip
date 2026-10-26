"""Voice data collection tool for keyword-spotting training data.

Single-user Flask app. Records 16 kHz mono PCM from one of these sources:
  - "browser": Web Audio API in the user's browser (needs HTTPS or localhost)
  - "pi_i2s":  INMP441 I2S mic read on the server via sounddevice (Raspberry Pi only, see pi_mic.py)
  - "esp32":   reserved for future uploads (POST /api/upload-recording with source=esp32)
stores the full recording as WAV and slices labelled 1.0 s clips from it.
Storage is plain files + CSV (no database).

cd ~/voicestrip/Data
source venv/bin/activate
python app.py --host 0.0.0.0

"""

import argparse
import csv
import re
import sys
import threading
import unicodedata
import wave
from array import array
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory

import pi_mic

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Internal key (ASCII, used in paths + CSV) -> display name (Estonian)
LABELS = {
    "sisse": "sisse",
    "valja": "välja",
    "punane": "punane",
    "roheline": "roheline",
    "sinine": "sinine",
    "valge": "valge",
    "hajuv": "hajuv",
    "vilkuv": "vilkuv",
    "heledam": "heledam",
    "tumedam": "tumedam",
    "taust": "taust",
    "kone": "kõne",
}

# Muted, distinct colour per label - shared by every page via the template context
LABEL_COLORS = {
    "sisse": "#2a9d8f",
    "valja": "#9c6644",
    "punane": "#d1495b",
    "roheline": "#52a35a",
    "sinine": "#3d74c9",
    "valge": "#8fa9c4",
    "hajuv": "#8e6cc9",
    "vilkuv": "#e07a3e",
    "heledam": "#c9a227",
    "tumedam": "#4a4e69",
    "taust": "#7a6f62",
    "kone": "#7b4b94",
}

DEFAULT_LABEL = "punane"
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # bytes -> 16-bit PCM
CHANNELS = 1
CLIP_SEC = 1.0
SNAP_SEC = 0.05
DEFAULT_PEAKS = 2000
SOURCES = ("browser", "pi_i2s", "esp32")
PI_TEST_SECONDS = 3.0

BASE_DIR = Path(__file__).resolve().parent
REC_DIR = BASE_DIR / "recordings"
ORIGINAL_DIR = REC_DIR / "original"
CLIPS_DIR = REC_DIR / "clips"
LABELS_CSV = REC_DIR / "labels.csv"
RECORDINGS_CSV = REC_DIR / "recordings.csv"

CLIP_FIELDS = [
    "clip_id", "recording_id", "label", "start_sec",
    "clip_path", "recording_name", "source", "created_at",
]
RECORDING_FIELDS = [
    "recording_id", "recording_name", "default_label", "duration_sec",
    "original_path", "source", "created_at",
]

RECORDING_ID_RE = re.compile(r"^\d{8}_\d{3,}$")

app = Flask(__name__)
# Keep LABELS order in dropdowns / summaries (API JSON and the Jinja |tojson filter)
app.json.sort_keys = False
app.jinja_env.policies["json.dumps_kwargs"] = {"sort_keys": False}
_lock = threading.Lock()  # serialises CSV writes and recording-id allocation
mic = pi_mic.PiMic(pi_mic.detect_pi_mic())  # .available can turn False at runtime (see self_test)


# --------------------------------------------------------------------------- #
# Helpers: filesystem / CSV
# --------------------------------------------------------------------------- #

def ensure_dirs() -> None:
    ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
    for label in LABELS:
        (CLIPS_DIR / label).mkdir(parents=True, exist_ok=True)


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def append_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    """Append rows; create the file with a header if it doesn't exist yet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def rel_path(path: Path) -> str:
    """Path relative to recordings/, always with forward slashes (portable in CSV)."""
    return path.relative_to(REC_DIR).as_posix()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(text: str) -> str:
    """'Välja Õhtu test' -> 'valja_ohtu_test' (ASCII only, safe for any filesystem)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace(" ", "_")
    text = re.sub(r"[^a-z0-9_-]", "", text)
    text = re.sub(r"_+", "_", text).strip("_-")
    return text or "recording"


def next_recording_id() -> str:
    """YYYYMMDD_NNN with NNN = 1 + highest counter used today."""
    prefix = datetime.now().strftime("%Y%m%d")
    used = [path.stem for path in ORIGINAL_DIR.glob(f"{prefix}_*.wav")]
    active = mic.active_recording_id()  # Pi recording in progress: its WAV doesn't exist yet
    if active:
        used.append(active)
    highest = 0
    for stem in used:
        parts = stem.split("_")
        if parts[0] == prefix and len(parts) >= 2 and parts[1].isdigit():
            highest = max(highest, int(parts[1]))
    return f"{prefix}_{highest + 1:03d}"


def unique_recording_name(name: str, recording_id: str) -> str:
    """Append ' (2)', ' (3)', ... if the same name was already used today."""
    day = recording_id.split("_")[0]
    used = {
        r["recording_name"] for r in read_csv(RECORDINGS_CSV)
        if r["recording_id"].startswith(day + "_")
    }
    if name not in used:
        return name
    n = 2
    while f"{name} ({n})" in used:
        n += 1
    return f"{name} ({n})"


def find_original(recording_id: str) -> Path | None:
    matches = sorted(ORIGINAL_DIR.glob(f"{recording_id}_*.wav"))
    return matches[0] if matches else None


def find_recording_row(recording_id: str) -> dict | None:
    return next((r for r in read_csv(RECORDINGS_CSV) if r["recording_id"] == recording_id), None)


def next_clip_index(rows: list[dict], recording_id: str) -> int:
    """Highest N used in '<recording_id>_<N>_<label>' so far (0 if none).

    Uses the max rather than a count, so deleting a clip never causes a later
    clip to reuse (and overwrite) an existing clip_id.
    """
    prefix = recording_id + "_"
    highest = 0
    for r in rows:
        if r["recording_id"] == recording_id and r["clip_id"].startswith(prefix):
            n = r["clip_id"][len(prefix):].split("_")[0]
            if n.isdigit():
                highest = max(highest, int(n))
    return highest


def safe_rec_path(relative: str) -> Path:
    """Resolve a CSV-stored path and make sure it stays inside recordings/."""
    path = (REC_DIR / Path(relative)).resolve()
    if REC_DIR.resolve() not in path.parents:
        abort(400)
    return path


def clip_summary(row: dict) -> dict:
    return {
        "clip_id": row["clip_id"],
        "label": row["label"],
        "start_sec": float(row["start_sec"] or 0),
        "clip_path": row["clip_path"],
    }


# --------------------------------------------------------------------------- #
# Helpers: audio (stdlib wave only)
# --------------------------------------------------------------------------- #

def write_wav(path: Path, pcm: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPLE_WIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE):
            raise ValueError(f"Unexpected WAV format in {path.name}")
        return w.readframes(w.getnframes())


def slice_pcm(pcm: bytes, start_sec: float, length_sec: float = CLIP_SEC) -> bytes:
    """Cut exactly length_sec of audio; pad with silence if the source is too short."""
    frame_bytes = SAMPLE_WIDTH * CHANNELS
    n_frames = int(round(length_sec * SAMPLE_RATE))
    start = int(round(start_sec * SAMPLE_RATE)) * frame_bytes
    chunk = pcm[start:start + n_frames * frame_bytes]
    return chunk + b"\x00" * (n_frames * frame_bytes - len(chunk))


def compute_peaks(pcm: bytes, count: int) -> list[float]:
    """Max absolute amplitude per bucket, normalised to 0..1."""
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    if n == 0:
        return []
    count = max(1, min(count, n))
    step = n / count
    peaks = []
    for i in range(count):
        chunk = samples[int(i * step):max(int((i + 1) * step), int(i * step) + 1)]
        peaks.append(round(max(max(chunk), -min(chunk)) / 32768, 4))
    return peaks


def snap_and_clamp(start_sec: float, duration_sec: float) -> float:
    start = round(start_sec / SNAP_SEC) * SNAP_SEC
    start = min(max(start, 0.0), max(duration_sec - CLIP_SEC, 0.0))
    return round(start, 3)


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #

@app.context_processor
def inject_labels():
    return {"labels": LABELS, "label_colors": LABEL_COLORS, "default_label": DEFAULT_LABEL,
            "pi_mic_available": mic.available}


@app.get("/")
def recorder_page():
    return render_template("recorder.html", active="recorder")


@app.get("/playback")
def playback_page():
    return render_template("playback.html", active="playback")


@app.get("/pi-mic-test")
def pi_mic_test_page():
    return render_template("pi_mic_test.html", active="recorder", test_seconds=PI_TEST_SECONDS,
                           devices=pi_mic.input_devices() if not mic.available else [])


@app.get("/review/<recording_id>")
def review_page(recording_id):
    if not RECORDING_ID_RE.match(recording_id) or find_original(recording_id) is None:
        abort(404)
    return render_template("recorder.html", active="recorder", review_id=recording_id)


@app.get("/recordings/<path:filename>")
def serve_recording(filename):
    return send_from_directory(REC_DIR, filename)


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

def api_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def parse_peaks(value, default: int = DEFAULT_PEAKS) -> int:
    try:
        count = int(value if value is not None else default)
    except (TypeError, ValueError):
        count = default
    return min(max(count, 100), 20000)


def store_recording(pcm: bytes, name: str, default_label: str, source: str,
                    recording_id: str | None = None) -> dict:
    """Write an original WAV + its recordings.csv row. Shared by every source.
    `recording_id` is passed when it was reserved earlier (Pi recordings)."""
    name = name.strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
    with _lock:
        ensure_dirs()
        recording_id = recording_id or next_recording_id()
        name = unique_recording_name(name, recording_id)
        path = ORIGINAL_DIR / f"{recording_id}_{slugify(name)}.wav"
        write_wav(path, pcm)
        duration = len(pcm) / (SAMPLE_WIDTH * CHANNELS * SAMPLE_RATE)
        append_csv(RECORDINGS_CSV, RECORDING_FIELDS, [{
            "recording_id": recording_id,
            "recording_name": name,
            "default_label": default_label,
            "duration_sec": f"{duration:.3f}",
            "original_path": rel_path(path),
            "source": source,
            "created_at": now_iso(),
        }])
    return {"recording_id": recording_id, "recording_name": name,
            "duration_sec": round(duration, 3), "original_path": rel_path(path)}


@app.post("/api/upload-recording")
def upload_recording():
    # Raw Int16 PCM body + query-string fields (browser), or multipart with an
    # 'audio' file + form fields (handy for future ESP32 uploads).
    if "audio" in request.files:
        pcm = request.files["audio"].read()
        fields = request.form
    else:
        pcm = request.get_data()
        fields = request.args

    pcm = pcm[: len(pcm) - (len(pcm) % SAMPLE_WIDTH)]
    if not pcm:
        return api_error("Empty recording")

    default_label = fields.get("default_label", DEFAULT_LABEL)
    if default_label not in LABELS:
        return api_error(f"Unknown label: {default_label}")
    source = fields.get("source", "browser")
    result = store_recording(pcm, fields.get("name") or "", default_label, source)
    return jsonify({**result, "waveform": compute_peaks(pcm, parse_peaks(fields.get("peaks")))})


# --------------------------------------------------------------------------- #
# Pi I2S microphone (server-side recording)
# --------------------------------------------------------------------------- #

@app.get("/api/sources")
def list_sources():
    return jsonify({"browser": True, "pi_i2s": mic.available, "esp32": False})


def pi_error(exc: Exception):
    """PiMicError = expected (409); anything else = hardware/driver failure, never a crash."""
    if isinstance(exc, pi_mic.PiMicError):
        return api_error(str(exc), 409)
    print(f"Pi mic error: {type(exc).__name__}: {exc}", flush=True)
    return api_error(f"Mikrofoni viga: {exc}. Kontrolli juhtmeid ja I2S seadistust (/pi-mic-test).", 503)


@app.post("/api/pi/start-recording")
def pi_start_recording():
    if not mic.available:
        return api_error("Pi I2S mikrofoni pole sellel seadmel saadaval", 404)
    data = request.get_json(silent=True) or {}
    default_label = str(data.get("default_label") or DEFAULT_LABEL)
    if default_label not in LABELS:
        return api_error(f"Unknown label: {default_label}")
    try:
        with _lock:  # reserve the id while the stream opens
            recording_id = next_recording_id()
            started_at = mic.start(recording_id, str(data.get("name") or ""), default_label)
    except Exception as exc:  # noqa: BLE001
        return pi_error(exc)
    print(f"Started Pi recording {recording_id}", flush=True)
    return jsonify({"recording_id": recording_id, "started_at": started_at})


@app.get("/api/pi/recording-status")
def pi_recording_status():
    return jsonify(mic.status())


@app.post("/api/pi/stop-recording")
def pi_stop_recording():
    data = request.get_json(silent=True) or {}
    recording_id = str(data.get("recording_id", ""))
    try:
        audio, meta = mic.stop(recording_id)
    except Exception as exc:  # noqa: BLE001
        return pi_error(exc)
    pcm = pi_mic.to_pcm16(audio)
    result = store_recording(pcm, meta["name"], meta["default_label"], "pi_i2s", recording_id=recording_id)
    print(f"Stopped Pi recording {recording_id} ({result['duration_sec']:.1f} s)"
          + (f" - {meta['warning']}" if meta["warning"] else ""), flush=True)
    return jsonify({**result, "warning": meta["warning"],
                    "waveform": compute_peaks(pcm, parse_peaks(data.get("peaks")))})


@app.get("/api/pi/peak")
def pi_peak():
    try:
        return jsonify({"peak": round(mic.current_peak(), 4), "recording": mic.active_recording_id() is not None,
                        "buffer_lag_ms": mic.current_lag_ms()})
    except Exception as exc:  # noqa: BLE001
        return pi_error(exc)


@app.post("/api/pi/test-record")
def pi_test_record():
    """Records PI_TEST_SECONDS and returns the WAV directly (nothing is saved)."""
    try:
        audio = mic.test_record(PI_TEST_SECONDS)
    except Exception as exc:  # noqa: BLE001
        return pi_error(exc)
    peak = float(abs(audio).max()) if len(audio) else 0.0
    return Response(pi_mic.wav_bytes(audio), mimetype="audio/wav", headers={"X-Peak": f"{peak:.4f}"})


@app.post("/api/save-clips")
def save_clips():
    data = request.get_json(silent=True) or {}
    recording_id = str(data.get("recording_id", ""))
    boxes = data.get("boxes") or []
    if not RECORDING_ID_RE.match(recording_id):
        return api_error("Invalid recording_id")
    if not isinstance(boxes, list) or not boxes:
        return api_error("No boxes to save")

    original = find_original(recording_id)
    if original is None:
        return api_error("Recording not found", 404)

    parsed = []
    for box in boxes:
        try:
            start = float(box["start_sec"])
            label = str(box["label"])
        except (KeyError, TypeError, ValueError):
            return api_error("Each box needs start_sec and label")
        if label not in LABELS:
            return api_error(f"Unknown label: {label}")
        parsed.append((start, label))

    pcm = read_wav(original)
    duration = len(pcm) / (SAMPLE_WIDTH * CHANNELS * SAMPLE_RATE)
    meta = find_recording_row(recording_id) or {}
    recording_name = meta.get("recording_name", original.stem)
    source = meta.get("source", "browser")

    with _lock:
        index = next_clip_index(read_csv(LABELS_CSV), recording_id)
        rows = []
        for start, label in parsed:
            start = snap_and_clamp(start, duration)
            index += 1
            clip_id = f"{recording_id}_{index}_{label}"
            clip_path = CLIPS_DIR / label / f"{clip_id}.wav"
            while clip_path.exists():  # orphan file from a manual edit - never overwrite
                index += 1
                clip_id = f"{recording_id}_{index}_{label}"
                clip_path = CLIPS_DIR / label / f"{clip_id}.wav"
            write_wav(clip_path, slice_pcm(pcm, start))
            rows.append({
                "clip_id": clip_id,
                "recording_id": recording_id,
                "label": label,
                "start_sec": f"{start:.2f}",
                "clip_path": rel_path(clip_path),
                "recording_name": recording_name,
                "source": source,
                "created_at": now_iso(),
            })
        append_csv(LABELS_CSV, CLIP_FIELDS, rows)

    return jsonify({
        "saved_count": len(rows),
        "clip_ids": [r["clip_id"] for r in rows],
        "clips": [clip_summary(r) for r in rows],
    })


@app.get("/api/recordings")
def list_recordings():
    clip_counts: dict[str, int] = {}
    for row in read_csv(LABELS_CSV):
        clip_counts[row["recording_id"]] = clip_counts.get(row["recording_id"], 0) + 1
    recordings = [
        {**r, "duration_sec": float(r["duration_sec"] or 0), "clip_count": clip_counts.get(r["recording_id"], 0)}
        for r in read_csv(RECORDINGS_CSV)
    ]
    recordings.sort(key=lambda r: r["created_at"], reverse=True)
    return jsonify({"recordings": recordings})


@app.get("/api/recording/<recording_id>")
def get_recording(recording_id):
    """Original recording + its already-saved clips (for review / preview)."""
    if not RECORDING_ID_RE.match(recording_id):
        return api_error("Invalid recording_id")
    original = find_original(recording_id)
    if original is None:
        return api_error("Recording not found", 404)

    try:
        peak_count = int(request.args.get("peaks", DEFAULT_PEAKS))
    except ValueError:
        peak_count = DEFAULT_PEAKS
    pcm = read_wav(original)
    duration = len(pcm) / (SAMPLE_WIDTH * CHANNELS * SAMPLE_RATE)
    meta = find_recording_row(recording_id) or {}
    clips = [clip_summary(r) for r in read_csv(LABELS_CSV) if r["recording_id"] == recording_id]
    clips.sort(key=lambda c: (c["start_sec"], c["clip_id"]))

    return jsonify({
        "recording_id": recording_id,
        "recording_name": meta.get("recording_name", original.stem),
        "default_label": meta.get("default_label", DEFAULT_LABEL),
        "created_at": meta.get("created_at", ""),
        "duration_sec": round(duration, 3),
        "original_path": rel_path(original),
        # peaks=0 skips the waveform (the recorder computes it from the WAV itself)
        "waveform": compute_peaks(pcm, min(peak_count, 20000)) if peak_count > 0 else [],
        "clips": clips,
    })


@app.delete("/api/recording/<recording_id>")
def delete_recording(recording_id):
    """Delete an original recording.

    Without ?all=1 (recorder 'Discard') it refuses when clips exist.
    With ?all=1 (review 'Kustuta originaal + kõik klipid') it also deletes every
    clip WAV and every labels.csv row belonging to the recording.
    """
    if not RECORDING_ID_RE.match(recording_id):
        return api_error("Invalid recording_id")
    delete_all = request.args.get("all") == "1"
    with _lock:
        clip_rows = read_csv(LABELS_CSV)
        own = [r for r in clip_rows if r["recording_id"] == recording_id]
        if own and not delete_all:
            return api_error("Recording has saved clips, not deleting", 409)
        for row in own:
            safe_rec_path(row["clip_path"]).unlink(missing_ok=True)
        if own:
            write_csv(LABELS_CSV, CLIP_FIELDS, [r for r in clip_rows if r["recording_id"] != recording_id])

        original = find_original(recording_id)
        if original is not None:
            original.unlink(missing_ok=True)
        rows = read_csv(RECORDINGS_CSV)
        kept = [r for r in rows if r["recording_id"] != recording_id]
        if len(kept) != len(rows):
            write_csv(RECORDINGS_CSV, RECORDING_FIELDS, kept)
    return jsonify({"deleted": recording_id, "deleted_clips": len(own), "had_original": original is not None})


@app.get("/api/clips")
def list_clips():
    label = request.args.get("label") or None
    source = request.args.get("source") or None
    if label is not None and label not in LABELS:
        return api_error(f"Unknown label: {label}")

    all_clips = read_csv(LABELS_CSV)
    counts = {key: 0 for key in LABELS}
    for row in all_clips:
        counts[row["label"]] = counts.get(row["label"], 0) + 1

    clips = [
        {**r, "start_sec": float(r["start_sec"] or 0), "duration_sec": CLIP_SEC}
        for r in all_clips
        if (label is None or r["label"] == label) and (source is None or r.get("source") == source)
    ]
    clips.sort(key=lambda r: (r["created_at"], r["clip_id"]), reverse=True)
    present = {r.get("source") or "" for r in all_clips} - {""}
    sources = list(SOURCES) + sorted(present - set(SOURCES))  # known ones first, then e.g. "upload"
    return jsonify({"clips": clips, "counts": counts, "total": len(all_clips), "sources": sources})


@app.delete("/api/clip/<clip_id>")
def delete_clip(clip_id):
    with _lock:
        rows = read_csv(LABELS_CSV)
        row = next((r for r in rows if r["clip_id"] == clip_id), None)
        if row is None:
            return api_error("Clip not found", 404)
        safe_rec_path(row["clip_path"]).unlink(missing_ok=True)
        write_csv(LABELS_CSV, CLIP_FIELDS, [r for r in rows if r["clip_id"] != clip_id])
    return jsonify({"deleted": clip_id})


# --------------------------------------------------------------------------- #

def lan_address() -> str | None:
    """This host's LAN IP (no packet is sent - a UDP connect only selects the route)."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Voice Collector")
    parser.add_argument("--host", default="127.0.0.1",
                        help="0.0.0.0 = reachable from other devices on the network (no login - trusted LAN only)")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    ensure_dirs()
    mic.self_test()
    print()
    print("  Voice Collector")
    print(f"  Salvestused: {REC_DIR}")
    print(f"  Pi I2S mikrofon: {'olemas' if mic.available else 'ei ole (ainult brauseri mikrofon)'}")
    print(f"  Ava Chrome ja mine: http://localhost:{args.port}")
    if args.host == "0.0.0.0":
        ip = lan_address()
        print(f"  Teistest seadmetest: http://{ip or '<selle-masina-ip>'}:{args.port}"
              "  (seal on brauseri mikrofon peidetud - see vajab HTTPS/localhost)")
        print("  NB: rakendusel pole parooli - kasuta ainult usaldusväärses võrgus.")
    print()
    app.run(host=args.host, port=args.port, debug=False, threaded=True)

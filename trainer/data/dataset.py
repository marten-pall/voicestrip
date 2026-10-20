from __future__ import annotations

import csv
import logging
import wave
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import torch
import torchaudio.functional as AF
from torch.utils.data import Dataset

from labels import LABELS

log = logging.getLogger(__name__)

WaveTransform = Callable[[torch.Tensor], torch.Tensor]


@dataclass(frozen=True)
class ClipItem:
    clip_id: str
    path: Path
    label: str | None        # None for unlabelled WAVs (eval on an arbitrary folder)
    recording_id: str
    source: str


def load_items(csv_path: Path, clips_dir: Path, classes: list[str] | None = None) -> list[ClipItem]:
    """Rows of the collector's labels.csv whose WAV exists and whose label is in `classes`
    (default: all LABELS); skips (and reports) the rest."""
    classes = LABELS if classes is None else classes
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    items: list[ClipItem] = []
    unknown_labels: Counter[str] = Counter()
    excluded: Counter[str] = Counter()
    missing: list[str] = []
    for row in rows:
        label = row["label"]
        if label not in LABELS:
            unknown_labels[label] += 1
            continue
        if label not in classes:
            excluded[label] += 1
            continue
        # clip_path in the CSV is relative to recordings/ ("clips/<label>/<id>.wav");
        # resolve against the configured clips_dir so the data folder can move.
        path = Path(clips_dir) / label / Path(row["clip_path"]).name
        if not path.is_file():
            missing.append(row["clip_id"])
            continue
        items.append(ClipItem(row["clip_id"], path, label, row["recording_id"], row.get("source", "")))

    if unknown_labels:
        log.warning("Skipped %d rows with labels not in LABELS: %s", sum(unknown_labels.values()), dict(unknown_labels))
    if excluded:
        log.info("Ignored %d clips of labels not in data.classes: %s", sum(excluded.values()), dict(excluded))
    if missing:
        preview = ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else "")
        log.warning("Skipped %d rows whose WAV file is missing: %s", len(missing), preview)
    return items


def load_wav(path: Path, sample_rate: int) -> torch.Tensor:
    """Mono float waveform [1, N] in [-1, 1] at `sample_rate`.

    16-bit PCM (what the collector writes) is read with the stdlib `wave` module,
    so no FFmpeg/TorchCodec backend is needed. Other WAV encodings fall back to
    torchaudio.load. Multi-channel audio is averaged to mono.
    """
    try:
        with wave.open(str(path), "rb") as w:
            if w.getsampwidth() != 2:
                raise wave.Error("not 16-bit PCM")
            channels, rate = w.getnchannels(), w.getframerate()
            frames = w.readframes(w.getnframes())
        audio = torch.frombuffer(bytearray(frames), dtype=torch.int16).float() / 32768.0
        audio = audio.reshape(-1, channels).T  # [C, N]
    except wave.Error:
        import torchaudio
        audio, rate = torchaudio.load(str(path))
    if audio.shape[0] > 1:
        audio = audio.mean(dim=0, keepdim=True)
    if rate != sample_rate:
        audio = AF.resample(audio, rate, sample_rate)
    return audio


def fix_length(wav: torch.Tensor, num_samples: int) -> torch.Tensor:
    """Zero-pad (end) or center-crop [1, N] to exactly num_samples."""
    n = wav.shape[-1]
    if n < num_samples:
        return torch.nn.functional.pad(wav, (0, num_samples - n))
    if n > num_samples:
        start = (n - num_samples) // 2
        return wav[..., start:start + num_samples]
    return wav


class VoiceDataset(Dataset):
    """Returns (waveform [1, num_samples], label_idx). label_idx is -1 for unlabelled items.

    `classes` defaults to the project's LABELS; pass another list to reuse the dataset
    for a different label set (e.g. Speech Commands pre-training).
    """

    def __init__(self, items: list[ClipItem], sample_rate: int, num_samples: int,
                 transform: WaveTransform | None = None, classes: list[str] | None = None) -> None:
        self.items = items
        self.sample_rate = sample_rate
        self.num_samples = num_samples
        self.transform = transform
        self.classes = list(classes) if classes is not None else LABELS
        self._class_idx = {label: i for i, label in enumerate(self.classes)}

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, int]:
        item = self.items[index]
        wav = fix_length(load_wav(item.path, self.sample_rate), self.num_samples)
        if self.transform is not None:
            wav = self.transform(wav)
        return wav, self._class_idx.get(item.label, -1)

    def class_counts(self) -> list[int]:
        counts = Counter(item.label for item in self.items)
        return [counts.get(label, 0) for label in self.classes]

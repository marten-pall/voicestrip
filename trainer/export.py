"""Export a trained checkpoint for the ESP32 firmware (../esp32/voicestrip/).

    python export.py --checkpoint runs/<run>/best.pt                    # everything
    python export.py --checkpoint runs/<run>/best.pt --features-only    # skip TFLite (works on Windows)

1. features  (needs only torch)
   features.h   log-mel constants: Hann window, mel filterbank, normalisation, class names.
                The firmware recomputes exactly the training features from these. Before writing,
                a re-implementation of the firmware's algorithm is checked against the trainer's
                extractor on real clips, so a mismatch is caught here and not on the device.
   test_clip.h  one test clip + the features and probabilities the firmware must reproduce
                (flash with KWS_SELFTEST 1 to compare on the device).

2. model     (needs litert-torch, formerly ai-edge-torch: Linux / WSL, Python 3.12)
   ../models/voicestrip.tflite   int8 model (or float32 with --float)
   model_data.h                  the same bytes as a C array
   The TFLite model's accuracy on the test split is compared with PyTorch's.

3. ../models/voicestrip.pt   copy of the checkpoint = the published model.

features.h, model_data.h and test_clip.h carry the same EXPORT_ID, and the firmware refuses
to compile if they come from different exports.
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import time
from pathlib import Path

import torch

from data import VoiceDataset, load_items, select_by_ids
from engine import load_checkpoint
from utils.logger import setup_logging

TRAINER_DIR = Path(__file__).resolve().parent
REPO_DIR = TRAINER_DIR.parent
DEFAULT_OUT = REPO_DIR / "esp32" / "voicestrip"
MODELS_DIR = REPO_DIR / "models"
PARITY_TOLERANCE = 1e-3   # max |firmware features - trainer features| (normalised log-mel units)
NON_COMMANDS = ("taust", "kone")

log = setup_logging()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="firmware sketch folder")
    parser.add_argument("--features-only", action="store_true", help="skip the TFLite conversion")
    parser.add_argument("--float", action="store_true", help="float32 TFLite instead of int8 (bigger, slower)")
    parser.add_argument("--calibration-clips", type=int, default=300, help="train clips for int8 calibration")
    return parser.parse_args()


# --------------------------------------------------------------------------- #
# Features: exactly what the firmware computes (kws.cpp), in torch
# --------------------------------------------------------------------------- #

class FirmwareFeatures:
    """Constants + algorithm of the firmware's log-mel. Mirrors kws.cpp line by line:
    frames of n_fft samples every hop samples (no centre padding), Hann window zero-padded
    to n_fft, |rfft|^2, mel filterbank, log(mel + eps), (x - mean) / std."""

    def __init__(self, extractor, sample_rate: int) -> None:
        mel = extractor.mel
        spec = mel.spectrogram
        if spec.center:
            raise SystemExit("features.center must be false for the ESP32 (retrain with the current config)")
        if spec.n_fft & (spec.n_fft - 1):
            raise SystemExit(f"features.n_fft must be a power of two for esp-dsp (got {spec.n_fft})")
        if spec.power != 2.0 or spec.normalized:
            raise SystemExit("only power=2, normalized=False spectrograms are supported")
        self.sample_rate = sample_rate
        self.n_fft = spec.n_fft
        self.hop = spec.hop_length
        self.win_length = spec.win_length
        self.n_bins = self.n_fft // 2 + 1
        window = spec.window.detach().cpu().float()            # Hann, periodic, win_length long
        left = (self.n_fft - self.win_length) // 2              # torch.stft centres a short window
        self.window = torch.zeros(self.n_fft)
        self.window[left:left + self.win_length] = window
        self.fb = mel.mel_scale.fb.detach().cpu().float()       # [n_bins, n_mels]
        self.n_mels = self.fb.shape[1]
        self.log_eps = extractor.log_eps
        if extractor.normalize:
            self.mean = extractor.mean.detach().cpu().float().squeeze(1)
            self.std = extractor.std.detach().cpu().float().squeeze(1)
        else:
            self.mean, self.std = torch.zeros(self.n_mels), torch.ones(self.n_mels)

    def n_frames(self, num_samples: int) -> int:
        return 1 + (num_samples - self.n_fft) // self.hop

    def __call__(self, pcm: torch.Tensor) -> torch.Tensor:
        """pcm: [num_samples] float in [-1, 1] -> [n_mels, n_frames]."""
        frames = pcm.unfold(0, self.n_fft, self.hop) * self.window        # [n_frames, n_fft]
        spectrum = torch.fft.rfft(frames)
        power = spectrum.real ** 2 + spectrum.imag ** 2                    # [n_frames, n_bins]
        logmel = torch.log(power @ self.fb + self.log_eps)                 # [n_frames, n_mels]
        return ((logmel - self.mean) / self.std).T

    def sparse_filterbank(self) -> tuple[list[int], list[int], list[float]]:
        """Per mel band: first FFT bin, number of bins, and the non-zero weights back to back."""
        starts, lengths, weights = [], [], []
        for m in range(self.n_mels):
            nz = torch.nonzero(self.fb[:, m]).flatten()
            first, last = (int(nz[0]), int(nz[-1])) if len(nz) else (0, -1)
            starts.append(first)
            lengths.append(last - first + 1)
            weights.extend(self.fb[first:last + 1, m].tolist())
        return starts, lengths, weights


def check_parity(feat: FirmwareFeatures, extractor, dataset: VoiceDataset, count: int = 32) -> float:
    worst = 0.0
    with torch.no_grad():
        for i in range(min(count, len(dataset))):
            wav, _ = dataset[i]
            pcm = quantize_pcm(wav[0])                       # same int16 input the device gets
            ref = extractor(pcm.view(1, 1, -1))[0, 0]        # trainer: [n_mels, n_frames]
            mine = feat(pcm)
            if ref.shape != mine.shape:
                raise SystemExit(f"feature shape mismatch: trainer {tuple(ref.shape)} vs firmware {tuple(mine.shape)}")
            worst = max(worst, (ref - mine).abs().max().item())
    return worst


def quantize_pcm(wav: torch.Tensor) -> torch.Tensor:
    """What the ESP32 sees: int16 samples / 32768."""
    return torch.clamp(torch.round(wav * 32768), -32768, 32767) / 32768


# --------------------------------------------------------------------------- #
# C headers
# --------------------------------------------------------------------------- #

def c_float(value: float) -> str:
    text = f"{float(value):.9g}"
    if not any(c in text for c in ".e"):
        text += ".0"  # "0f" is not a valid C literal, "0.0f" is
    return text + "f"


def c_floats(values, per_line: int = 8) -> str:
    items = [c_float(v) for v in values]
    return ",\n".join("  " + ", ".join(items[i:i + per_line]) for i in range(0, len(items), per_line))


def c_ints(values, per_line: int = 16) -> str:
    items = [str(int(v)) for v in values]
    return ",\n".join("  " + ", ".join(items[i:i + per_line]) for i in range(0, len(items), per_line))


def header(title: str, export_id: int, source: str) -> str:
    return (f"// {title}\n// Generated by trainer/export.py from {source} - do not edit by hand.\n"
            f"#pragma once\n#include <stdint.h>\n\n")


def write_features_h(path: Path, feat: FirmwareFeatures, classes: list[str], num_samples: int,
                     export_id: int, source: str) -> None:
    starts, lengths, weights = feat.sparse_filterbank()
    offsets, total = [], 0
    for n in lengths:
        offsets.append(total)
        total += n
    names = ", ".join(f'"{c}"' for c in classes)
    commands = ", ".join("0" if c in NON_COMMANDS else "1" for c in classes)
    text = header("Log-mel feature constants for the voicestrip keyword model", export_id, source) + f"""\
#define FEATURES_EXPORT_ID {export_id}UL

constexpr int FEAT_SAMPLE_RATE = {feat.sample_rate};
constexpr int FEAT_NUM_SAMPLES = {num_samples};   // 1 s analysis window
constexpr int FEAT_N_FFT = {feat.n_fft};
constexpr int FEAT_HOP = {feat.hop};
constexpr int FEAT_N_BINS = {feat.n_bins};
constexpr int FEAT_N_MELS = {feat.n_mels};
constexpr int FEAT_N_FRAMES = {feat.n_frames(num_samples)};
constexpr float FEAT_LOG_EPS = {c_float(feat.log_eps)};

constexpr int KWS_N_CLASSES = {len(classes)};
static const char* const KWS_CLASS_NAMES[KWS_N_CLASSES] = {{{names}}};
// 1 = a command that changes the lights, 0 = "taust" / "kone" (never acted on)
static const uint8_t KWS_IS_COMMAND[KWS_N_CLASSES] = {{{commands}}};

// Hann window ({feat.win_length} samples) zero-padded to FEAT_N_FFT, as torch.stft does
static const float FEAT_WINDOW[FEAT_N_FFT] = {{
{c_floats(feat.window.tolist())}
}};

// Mel filterbank, sparse: band m uses bins MEL_START[m] .. MEL_START[m] + MEL_LEN[m] - 1
// with weights MEL_WEIGHTS[MEL_OFFSET[m] ...]
static const uint16_t MEL_START[FEAT_N_MELS] = {{
{c_ints(starts)}
}};
static const uint16_t MEL_LEN[FEAT_N_MELS] = {{
{c_ints(lengths)}
}};
static const uint16_t MEL_OFFSET[FEAT_N_MELS] = {{
{c_ints(offsets)}
}};
static const float MEL_WEIGHTS[{total}] = {{
{c_floats(weights)}
}};

// Per-band normalisation fitted on the training set: x = (log_mel - MEAN) * INV_STD
static const float FEAT_MEAN[FEAT_N_MELS] = {{
{c_floats(feat.mean.tolist())}
}};
static const float FEAT_INV_STD[FEAT_N_MELS] = {{
{c_floats((1.0 / feat.std).tolist())}
}};
"""
    path.write_text(text, encoding="utf-8", newline="\n")


def write_test_clip_h(path: Path, pcm16: torch.Tensor, features: torch.Tensor, probs: list[float],
                      label: str, probs_source: str, export_id: int, source: str) -> None:
    text = header("Self-test clip (KWS_SELFTEST 1)", export_id, source) + f"""\
#define TEST_CLIP_EXPORT_ID {export_id}UL

static const char* const TEST_CLIP_LABEL = "{label}";
static const char* const TEST_CLIP_PROBS_SOURCE = "{probs_source}";
static const int16_t TEST_CLIP_PCM[{len(pcm16)}] = {{
{c_ints(pcm16.tolist())}
}};
// expected normalised log-mel, [mel][frame]
static const float TEST_CLIP_FEATURES[{features.numel()}] = {{
{c_floats(features.flatten().tolist())}
}};
static const float TEST_CLIP_PROBS[{len(probs)}] = {{
{c_floats(probs)}
}};
"""
    path.write_text(text, encoding="utf-8", newline="\n")


def write_model_h(path: Path, tflite: bytes, classes: list[str], export_id: int, source: str) -> None:
    lines = ",\n".join("  " + ", ".join(f"0x{b:02x}" for b in tflite[i:i + 16]) for i in range(0, len(tflite), 16))
    text = header(f"TFLite model ({len(tflite)} bytes), classes: {', '.join(classes)}", export_id, source) + f"""\
#define MODEL_EXPORT_ID {export_id}UL

alignas(16) static const unsigned char g_model_data[] = {{
{lines}
}};
static const unsigned int g_model_data_len = {len(tflite)};
"""
    path.write_text(text, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------- #
# TFLite conversion + check
# --------------------------------------------------------------------------- #

def import_converter():
    for name in ("litert_torch", "ai_edge_torch"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise SystemExit("TFLite conversion needs litert-torch (Linux/WSL, Python 3.12):\n"
                     "    pip install litert-torch\n"
                     "or run with --features-only to export only features.h / test_clip.h.")


def convert(model: torch.nn.Module, sample: torch.Tensor, calibration: list[torch.Tensor],
            as_float: bool) -> bytes:
    lt = import_converter()
    out = TRAINER_DIR / ".export_tmp.tflite"
    if as_float:
        lt.convert(model, (sample,)).export(str(out))
    else:
        from torch.ao.quantization.quantize_pt2e import convert_pt2e, prepare_pt2e
        pq = importlib.import_module(f"{lt.__name__}.quantize.pt2e_quantizer")
        qc = importlib.import_module(f"{lt.__name__}.quantize.quant_config")
        quantizer = pq.PT2EQuantizer().set_global(
            pq.get_symmetric_quantization_config(is_per_channel=True, is_dynamic=False))
        export_fn = getattr(torch.export, "export_for_training", torch.export.export)
        graph = export_fn(model, (sample,)).module()
        graph = prepare_pt2e(graph, quantizer)
        with torch.no_grad():
            for x in calibration:
                graph(x)
        graph = convert_pt2e(graph, fold_quantize=False)
        lt.convert(graph, (sample,), quant_config=qc.QuantConfig(pt2e_quantizer=quantizer)).export(str(out))
    data = out.read_bytes()
    out.unlink()
    return data


class TfliteRunner:
    def __init__(self, tflite: bytes) -> None:
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError:
            from tensorflow.lite import Interpreter  # type: ignore[no-redef]
        import numpy as np
        self.np = np
        self.it = Interpreter(model_content=tflite)
        self.it.allocate_tensors()
        self.inp = self.it.get_input_details()[0]
        self.out = self.it.get_output_details()[0]
        log.info("TFLite input %s %s, output %s %s", self.inp["shape"], self.inp["dtype"].__name__,
                 self.out["shape"], self.out["dtype"].__name__)
        try:
            ops = sorted({op["op_name"] for op in self.it._get_ops_details()})
            log.info("TFLite ops (the firmware's op resolver must include all): %s", ", ".join(ops))
        except Exception:  # noqa: BLE001 - private API, informational only
            pass

    def probs(self, features: torch.Tensor) -> list[float]:
        """features [n_mels, n_frames] -> softmax probabilities (the firmware does the same)."""
        np = self.np
        x = features.numpy().astype(np.float32).reshape(self.inp["shape"])  # C=1: NCHW and NHWC share the order
        if self.inp["dtype"] == np.int8:
            scale, zero = self.inp["quantization"]
            x = np.clip(np.round(x / scale) + zero, -128, 127).astype(np.int8)
        self.it.set_tensor(self.inp["index"], x)
        self.it.invoke()
        y = self.it.get_tensor(self.out["index"]).astype(np.float32).flatten()
        if self.out["dtype"] == np.int8:
            scale, zero = self.out["quantization"]
            y = (y - zero) * scale
        return torch.softmax(torch.from_numpy(y), 0).tolist()


# --------------------------------------------------------------------------- #

def main() -> None:
    args = parse_args()
    model, extractor, cfg, ckpt = load_checkpoint(args.checkpoint, torch.device("cpu"), TRAINER_DIR)
    classes: list[str] = ckpt["classes"]
    if cfg.features.type != "melspec":
        raise SystemExit("the firmware implements features.type=melspec only")
    export_id = int(time.time())
    source = f"{args.checkpoint.parent.name}/{args.checkpoint.name} (epoch {ckpt['epoch']})"
    log.info("Checkpoint %s, classes: %s", source, ", ".join(classes))

    all_items = load_items(cfg.data.csv_path, cfg.data.clips_dir, classes)
    sr, num_samples = cfg.data.sample_rate, cfg.data.num_samples
    test_ds = VoiceDataset(select_by_ids(all_items, ckpt["split_ids"]["test"]), sr, num_samples, classes=classes)
    train_ds = VoiceDataset(select_by_ids(all_items, ckpt["split_ids"]["train"]), sr, num_samples, classes=classes)
    if not len(test_ds):
        raise SystemExit("the checkpoint's test split has no clips on this machine (data missing?)")

    # 1. features
    feat = FirmwareFeatures(extractor, sr)
    worst = check_parity(feat, extractor, test_ds)
    log.info("Feature parity (firmware algorithm vs trainer extractor): max |diff| = %.2e", worst)
    if worst > PARITY_TOLERANCE:
        raise SystemExit(f"feature mismatch {worst:.2e} > {PARITY_TOLERANCE} - the firmware would see different input")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_features_h(args.out_dir / "features.h", feat, classes, num_samples, export_id, source)
    log.info("Wrote %s (%d mels x %d frames)", args.out_dir / "features.h", feat.n_mels, feat.n_frames(num_samples))

    def features_of(index: int, ds: VoiceDataset) -> tuple[torch.Tensor, int]:
        wav, target = ds[index]
        return feat(quantize_pcm(wav[0])), target

    # 2. model
    runner = None
    if not args.features_only:
        sample = features_of(0, train_ds)[0].view(1, 1, feat.n_mels, -1)
        step = max(1, len(train_ds) // args.calibration_clips)
        calibration = [features_of(i, train_ds)[0].view(1, 1, feat.n_mels, -1) for i in range(0, len(train_ds), step)]
        log.info("Converting to TFLite (%s, %d calibration clips)...", "float32" if args.float else "int8",
                 0 if args.float else len(calibration))
        tflite = convert(model, sample, calibration, args.float)
        runner = TfliteRunner(tflite)
        torch_ok = tfl_ok = agree = 0
        with torch.no_grad():
            for i in range(len(test_ds)):
                x, target = features_of(i, test_ds)
                p_torch = int(model(x.view(1, 1, feat.n_mels, -1)).argmax())
                p_tfl = max(range(len(classes)), key=runner.probs(x).__getitem__)
                torch_ok += p_torch == target
                tfl_ok += p_tfl == target
                agree += p_torch == p_tfl
        n = len(test_ds)
        log.info("Test split (%d clips): PyTorch %.1f%%, TFLite %.1f%%, same prediction %.1f%%",
                 n, 100 * torch_ok / n, 100 * tfl_ok / n, 100 * agree / n)
        MODELS_DIR.mkdir(exist_ok=True)
        (MODELS_DIR / "voicestrip.tflite").write_bytes(tflite)
        write_model_h(args.out_dir / "model_data.h", tflite, classes, export_id, source)
        log.info("Wrote %s and %s (%.1f KB)", MODELS_DIR / "voicestrip.tflite", args.out_dir / "model_data.h",
                 len(tflite) / 1024)

    # self-test clip: the first test clip of a command class (fallback: any)
    index = next((i for i, it in enumerate(test_ds.items) if it.label not in NON_COMMANDS), 0)
    wav, target = test_ds[index]
    pcm16 = torch.clamp(torch.round(wav[0] * 32768), -32768, 32767).to(torch.int16)
    x = feat(pcm16.float() / 32768)
    if runner is not None:
        probs, probs_source = runner.probs(x), "tflite"
    else:
        with torch.no_grad():
            probs = torch.softmax(model(x.view(1, 1, feat.n_mels, -1))[0], 0).tolist()
        probs_source = "pytorch"
    write_test_clip_h(args.out_dir / "test_clip.h", pcm16, x, probs, classes[target], probs_source, export_id, source)
    log.info("Wrote %s (label %s, expected top: %s %.2f)", args.out_dir / "test_clip.h", classes[target],
             classes[max(range(len(probs)), key=probs.__getitem__)], max(probs))

    # 3. publish the checkpoint next to the TFLite model
    if runner is not None:
        shutil.copyfile(args.checkpoint, MODELS_DIR / "voicestrip.pt")
        log.info("Copied checkpoint to %s", MODELS_DIR / "voicestrip.pt")
    else:
        log.info("--features-only: model_data.h and ../models/ were not touched. If model_data.h exists from "
                 "another export, the firmware refuses to compile until both come from the same export.")


if __name__ == "__main__":
    main()

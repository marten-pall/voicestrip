import sys
from pathlib import Path

TRAINER_DIR = Path(__file__).parent / "trainer"
sys.path.insert(0, str(TRAINER_DIR))

import torch
import numpy as np
from gpiozero import PWMLED

from models.my_model import MyModel
from features.extractors import MelSpectrogramExtractor
from utils.config import FeaturesConfig
from Data.pi_mic import PiMic, detect_pi_mic


# ========== Setup ==========
BEST_PT = Path(__file__).parent / "trainer" / "best.pt"
checkpoint = torch.load(BEST_PT, map_location='cpu', weights_only=False)

LABELS = checkpoint['classes']
cfg = checkpoint['config']
print(type(cfg))
print(cfg)
print(f"Model loaded: {len(LABELS)} classes = {LABELS}")
print(f"Val acc: {checkpoint['val_acc']:.3f} @ epoch {checkpoint['epoch']}")

# Extractor sama config'iga mis treeningul + lae normalization stats
extractor = MelSpectrogramExtractor(cfg=FeaturesConfig(**cfg['features']),
    sample_rate=cfg['data']['sample_rate'])
extractor.load_state_dict(checkpoint['extractor_state'])
extractor.eval()

# Mudel
model = MyModel(num_classes=len(LABELS))
model.load_state_dict(checkpoint['model_state'])
model.eval()

# Mikrofon
mic = PiMic(available=detect_pi_mic())
mic.self_test()

# LED-id (R=GPIO26, G=GPIO12, B=GPIO13 — sinu testitud mapping)
R = PWMLED(26); G = PWMLED(12); B = PWMLED(13)

COLOR_MAP = {
    "punane": (255, 0, 0),
    "roheline": (0, 255, 0),
    "sinine": (0, 0, 255),
    "valge": (255, 255, 255),
}


# ========== Helpers ==========
def set_rgb(r: int, g: int, b: int) -> None:
    """Sea RGB 0-255. Loogika pööratud (level shifter)."""
    R.value = (255 - r) / 255
    G.value = (255 - g) / 255
    B.value = (255 - b) / 255


def execute(label: str) -> None:
    if label in COLOR_MAP:
        set_rgb(*COLOR_MAP[label])
    elif label == "valja":
        set_rgb(0, 0, 0)
    elif label == "sisse":
        set_rgb(255, 255, 255)
    # kone, taust → ei tee midagi


def record_1s() -> torch.Tensor:
    """Salvesta 1s mikrofonist, tagasta torch tensor [16000]."""
    audio_np = mic.test_record(1.0)
    return torch.from_numpy(audio_np).float()


def predict(audio: torch.Tensor) -> tuple[str, float]:
    """audio: [N] -> (label, confidence)."""
    # Extractor ootab [B, 1, N]
    wav = audio.unsqueeze(0).unsqueeze(0)
    features = extractor(wav)  # [1, 1, n_mels, n_frames]

    with torch.no_grad():
        logits = model(features)  # [1, num_classes]
        probs = torch.softmax(logits, dim=1)
        confidence, idx = probs.max(dim=1)

    return LABELS[idx.item()], confidence.item()


# ========== Main loop ==========
def listen_and_act() -> None:
    set_rgb(0, 0, 0)
    print("\nKuulan... (Ctrl+C katkestamiseks)\n")

    try:
        while True:
            audio = record_1s()
            label, conf = predict(audio)

            if conf >= 0.35:
                print(f"Prediction: {label} ({conf:.2f})")
                execute(label)
            else:
                print(f"  low conf: {label} ({conf:.2f})")

    except KeyboardInterrupt:
        print("\nSuletud.")
        set_rgb(0, 0, 0)
        R.close(); G.close(); B.close()


if __name__ == "__main__":
    listen_and_act()

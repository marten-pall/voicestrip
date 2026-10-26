import torch
from gpiozero import PWMLED
import sounddevice as sd
import numpy as np
from Data.pi_mic import PiMic, detect_pi_mic

from models.my_model import MyModel
from features.extractors import MelSpectrogramExtractor
LABELS = ["kone", "sisse", "taust", "valja"]
#LABELS = ["kone", "sisse", "taust", "valja", "punane", "roheline", "sinine", "valge","hajuv", "heledam", "tumedam"]
COLOR_MAP = {"punane" : (255,0,0),
             "roheline" : (0,0,255)}

model = MyModel(num_classes=len(LABELS))
model.load_state_dict(torch.load('best.pt', map_location='cpu'))
model.eval()

extractor = MelSpectrogramExtractor(sample_rate=16000,n_mels = 40, n_fft = 400)
mic = PiMic(available=(detect_pi_mic()))
mic.self_test()

R = PWMLED(26); G = PWMLED(12); B = PWMLED(13)

def set_rgb(r,g,b):
    R.value = (255-r)/255
    G.value = (255-g)/255
    B.value = (255-b)/255

def execute(label):
    if label in COLOR_MAP:
        set_rgb(*COLOR_MAP[label])
    elif label == "kone" or label == "taust" or label == "valja":
        set_rgb(0,0,0)
    elif label == "sisse":
        set_rgb(255,255,255)

def record_1s():
    "Salvestab 1s, tagastab float32 numpy array 16kHz"
    return mic.test_record(1)

def listen_and_act():
    while True:
        audio = record_1s()
        audio_tensor = torch.from_numpy(audio).float()
        features = extractor(audio_tensor)

        with torch.no_grad():
            logits = model(features.unsqueeze(0))
            probs = torch.softmax(logits, dim=1)
            confidence, pred_idx = probs.max(dim=1)

        if confidence >= 0.85:
            label = LABELS[pred_idx.item()]
            print(f"Prediction: {label}, confidence: {confidence:.2f}")
            execute(label)

if __name__ == "__main__":
    listen_and_act()
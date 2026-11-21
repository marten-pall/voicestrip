# voicestrip

**A voice-controlled RGB LED strip.** Say *"sisse"*, *"punane"* or *"välja"* (Estonian for *on*,
*red*, *off*) and the strip reacts. Speech is recognised **on the ESP32-S3 itself** by a small
neural network: no cloud, no WiFi, no phone app.

🇪🇪 [Eesti keeles: README.et.md](README.et.md)

- **Build one:** buy the parts, solder the board, flash the firmware with the published model.
  No training needed.
- **Make it yours:** record your own voice (or new words) with the browser-based collector,
  train the CNN in PyTorch, export it to the ESP32.

> **Status:** hardware, firmware, collector, training and export pipeline work. The final
> trained model (`models/voicestrip.pt` + `esp32/voicestrip/model_data.h`) is published once
> enough data is collected. Until then, train your own (section 4).

---

## Contents

1. [Commands](#1-commands)
2. [Parts](#2-parts)
3. [Build and flash (published model)](#3-build-and-flash)
4. [Train your own model](#4-train-your-own-model)
5. [How it works](#5-how-it-works)
6. [Troubleshooting](#6-troubleshooting)
7. [Repository layout](#7-repository-layout)

---

## 1. Commands

| Say | Meaning | The strip |
|---|---|---|
| **sisse** | on | turns on (last colour, white at first) |
| **välja** | off | turns off |
| **punane** | red | red |
| **roheline** | green | green |
| **sinine** | blue | blue |
| **valge** | white | white |
| **hajuv** | fading | slow rainbow colour cycle |
| **vilkuv** | blinking | blinks the current colour |
| **heledam** | brighter | one step brighter |
| **tumedam** | dimmer | one step dimmer |

The model also has two non-command classes, `taust` (background noise) and `kone` (ordinary
speech), so that the strip ignores everything else. The published model only knows the
classes it was trained on (listed at boot on the Serial Monitor).

---

## 2. Parts

| # | Part | Example | Notes |
|---|---|---|---|
| 1 | Microcontroller | **ESP32-S3-DevKitC-1 N16R8** | 16 MB flash, **8 MB PSRAM required** (the model runs from PSRAM) |
| 1 | I2S MEMS microphone | **INMP441** breakout | |
| 1 | LED strip | 12 V **non-addressable** 5050 RGB (4 wires: +12V, R, G, B) | 5 m used here |
| 3 | N-channel MOSFET | IRFZ44N | one per colour |
| 3 | NPN transistor | S9013 (or 2N2222, BC337) | level shifter: 3.3 V GPIO → 12 V gate |
| 3 | Resistor 1 kΩ | | NPN base |
| 3 | Resistor 10 kΩ | | MOSFET gate pull-up to 12 V |
| 3 | Resistor 10 kΩ | *recommended* | GPIO pull-up to 3.3 V: keeps the strip **off** while the ESP32 boots |
| 1 | Buck converter | LM2596HV module | 12 V → 5 V for the ESP32 |
| 1 | Power supply | **12 V, ≥ 6 A** for 5 m of strip | see *Power* below |
| | Perfboard, wires, DC jack, screw terminals | | optional: 3D-printed case |

### Power

A 5 m 5050 RGB strip (60 LEDs/m) draws **about 1.2 A per metre at full white, ~6 A (72 W)**.
A weaker adapter goes into overload protection, switches off and on again (its LED blinks)
and the ESP32 keeps rebooting. Either use a 12 V ≥ 6 A adapter, or cap the brightness in
`config.h`: `#define LED_MAX_BRIGHTNESS 80` for a 2 A adapter.

### Wiring

**ESP32-S3**

| Signal | GPIO | |
|---|---|---|
| INMP441 SCK | 4 | |
| INMP441 WS | 3 | |
| INMP441 SD | 2 | |
| INMP441 VDD / GND | 3V3 / GND | |
| INMP441 L/R | GND | left channel |
| LED red | 9 | to the NPN base through 1 kΩ |
| LED green | 10 | to the NPN base through 1 kΩ |
| LED blue | 11 | to the NPN base through 1 kΩ |
| 5V | LM2596 OUT+ | **set the LM2596 to 5.0 V before connecting the ESP32** |
| GND | common ground | |

**One colour channel (build it three times):**

```
                         +12 V
                           │
                         10 kΩ (gate pull-up)
                           │
GPIO ──┬── 1 kΩ ──► S9013 base     S9013 collector ──┬── IRFZ44N gate
       │                           S9013 emitter ── GND
     10 kΩ (recommended,                             IRFZ44N drain ── LED strip R / G / B
       │    to 3.3 V)                                IRFZ44N source ── GND
     3.3 V

LED strip +12 V ── PSU +12 V (directly)
```

The NPN stage **inverts** the signal: GPIO HIGH → transistor on → gate pulled to GND →
LEDs **off**; GPIO LOW → LEDs on. The firmware knows this (`LED_ACTIVE_LOW 1`, default). If
you drive the MOSFET gates directly instead, set `#define LED_ACTIVE_LOW 0`.
Without the recommended 10 kΩ GPIO pull-ups the strip lights up **full white for a moment**
at every boot and upload (the GPIOs float until the firmware starts).

**Common ground:** PSU −, LM2596 IN−/OUT−, ESP32 GND, MOSFET sources and NPN emitters.

---

## 3. Build and flash

1. Install **[Arduino IDE 2](https://www.arduino.cc/en/software)**, then *Tools → Board →
   Boards Manager*: install **"esp32" by Espressif Systems, version 3.x** (tested: 3.3.12).
   It already includes TensorFlow Lite Micro and esp-dsp, no extra libraries are needed.
2. Clone or download this repository, then *File → Open →* `esp32/voicestrip/voicestrip.ino`.
   The window title must say **voicestrip** (each sketch opens in its own window).
3. *Tools* menu:

   | Setting | Value |
   |---|---|
   | Board | ESP32S3 Dev Module |
   | PSRAM | **OPI PSRAM** |
   | Flash Size | 16MB (128Mb) |
   | Partition Scheme | 16M Flash (3MB APP/9.9MB FATFS) |
   | USB CDC On Boot | **Disabled** if the cable is in the **COM/UART** port (CH340/CH343 chip), **Enabled** if in the native **USB** port |
   | Port | the COM port that appears when you plug the board in |

4. Optional settings go in `config.h`: copy `config.example.h` to `config.h` (brightness
   cap, threshold, pins, ...).
5. **Upload** (Ctrl+U). The output must mention `voicestrip.ino.bin`. If it says *"Failed to
   connect"*: hold **BOOT**, press and release **RESET**, release **BOOT**, upload again.
6. *Tools → Serial Monitor*, **115200** baud, press **RESET**:

   ```
   voicestrip voicestrip-2.0
   Helipuhver: 32.8 s (PSRAM)
   Režiim: häälkäsklused, klassid: hajuv heledam kone punane ... | lävi 0.80
   Mudel: … baiti, int8, arena … / 1536 KB
   Kuulan. Ütle nt "sisse".
   ```

   A short green flash means it is listening. Say **"sisse"**.

**Tuning** (`config.h`): `KWS_THRESHOLD` (default 0.80, higher = fewer false triggers),
`KWS_DEBUG 1` prints every inference, `KWS_SELFTEST 1` checks at boot that the ESP32
computes the same features and output as the PC did (`test_clip.h`).

---

## 4. Train your own model

The model in this repository learned *one* person's voice in *one* home. It works best for
voices and rooms like those. For your own voice, accent or new words, collect data and
train. It takes an evening or two.

### 4.1 Collect data

The **collector** (`Data/`) is a small Flask web app. You record a long take, then place
1-second boxes on the waveform over each word; it cuts them into labelled 16 kHz WAV clips
plus `labels.csv`. Record with the **ESP32's own microphone**, because the model works best
with the microphone it will listen through.

```bash
cd Data
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python app.py --host 0.0.0.0
```

The app prints this PC's LAN IP. Then:

1. ESP32 as a WiFi microphone: in `esp32/voicestrip/config.h` set

   ```cpp
   #define COLLECTOR_MODE 1
   #define WIFI_SSID     "your-2.4GHz-wifi"
   #define WIFI_PASSWORD "..."
   #define SERVER_HOST   "192.168.1.23"   // the IP app.py printed
   ```

   and upload. LEDs: blue blink = joining WiFi, yellow blink = server not reachable,
   green = ready, red = recording.
2. Open `http://localhost:5000`, choose **Allikas: ESP32 mikrofon** (green dot = online).
   Check the level on `/esp32-mic-test` first.
3. **RECORD** → say the words with pauses → **STOP** → place boxes (`B` key) → pick a label
   per box → **💾 Accept**. The browser microphone works too (only via `localhost`).

Windows: allow Python through the firewall (on *Private* networks, or *Public* if your WiFi
profile is public). Details and the CSV format: [`Data/README.md`](Data/README.md).

**How much data:** at least ~150 clips per command (300+ is good), from **15+ separate
recordings**, several people, distances and rooms. `taust` and `kone` should be the largest
classes (500–1000+): long recordings of silence, music, TV and normal talk. Otherwise the strip
reacts to ordinary speech.

### 4.2 Train

On a machine with PyTorch (a GPU helps, CPU works):

```bash
cd trainer
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install torch torchaudio     # CUDA build: see pytorch.org
pip install -r requirements.txt
python train.py -o "data.classes=[hajuv, heledam, kone, punane, roheline, sinine, sisse, taust, tumedam, valge, valja, vilkuv]" -o data.split_by=recording
```

All settings live in `trainer/utils/config.py`; `-o key=value` overrides one. Each run writes
`runs/<name>_<timestamp>/` with `best.pt`, metrics and a confusion matrix.
`split_by=recording` keeps whole recordings out of training for an honest test score.
Details: [`trainer/README.md`](trainer/README.md).

### 4.3 Export to the ESP32

The TFLite converter needs **Linux or WSL with Python 3.12**:

```bash
pip install litert-torch
python export.py --checkpoint runs/<run>/best.pt
```

It writes `esp32/voicestrip/features.h`, `model_data.h`, `test_clip.h` and
`models/voicestrip.{pt,tflite}`, after checking that

- the firmware's feature code gives the same numbers as training (max diff ~1e-6), and
- the int8 TFLite model agrees with PyTorch on the test split (accuracy is printed for both).

Then set `COLLECTOR_MODE 0` (or remove it) in `config.h` and upload again (section 3).
`python export.py ... --features-only` works on Windows too (no TFLite) for checking
features.

---

## 5. How it works

```
INMP441 ──I2S 16 kHz──► ring buffer ──every 250 ms: last 1 s──► log-mel 40×97 ──► CNN (int8, TFLite Micro)
                                                                                         │
                LED strip ◄── PWM ◄── command ◄── average of 3 ≥ 0.80, 1.5 s cooldown ◄──┘
```

- **Features:** 25 ms Hann window, 10 ms hop, 512-point FFT, 40 mel bands, log, per-band
  normalisation. The trainer (torchaudio) and the firmware (esp-dsp) compute them
  identically; `export.py` generates the constants and verifies this.
- **Model:** 2 × (Conv 3×3 → BatchNorm → ReLU), max-pool, global average, linear.
  Trained with time-shift, volume and background-noise augmentation plus SpecAugment.
- **Decisions:** an inference every 250 ms on a sliding 1 s window; three are averaged; a
  command needs ≥ 0.80 and then 1.5 s pass before the next one.

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| Whole strip full white, adapter LED blinking | Wrong logic level: `LED_ACTIVE_LOW` (section 2), and/or adapter too weak: `LED_MAX_BRIGHTNESS` |
| Strip flashes white at boot / upload | Add the 10 kΩ GPIO → 3.3 V pull-ups |
| Serial Monitor shows only `entry 0x403c88b8` | *USB CDC On Boot* does not match the USB port in use, or the wrong sketch was uploaded (check the `.ino.bin` name) |
| `Failed to connect to ESP32-S3` | BOOT + RESET (section 3, step 5) |
| `Voice control needs model_data.h` when compiling | No model exported yet: export one (4.3) or use `COLLECTOR_MODE 1` |
| `Mudeli mälu ... eraldamine ebaõnnestus` | *Tools → PSRAM → OPI PSRAM* |
| `Didn't find op for builtin opcode` | Add that op to the resolver in `kws.cpp` (`resolver.Add...`) |
| Reacts to normal talk / TV | Raise `KWS_THRESHOLD`; record more `kone` and `taust` |
| Never reacts | `KWS_DEBUG 1` and watch the probabilities; check the mic level in collector mode on `/esp32-mic-test` |
| Mic level always exactly 0 | SD/WS/SCK wiring; L/R to GND, or `#define I2S_MIC_SLOT I2S_STD_SLOT_RIGHT` |
| Collector: ESP32 dot stays grey, LED blinks yellow | Wrong `SERVER_HOST` (PC IP changed?), `app.py` without `--host 0.0.0.0`, or firewall |
| Collector: LED blinks blue | WiFi name/password, 2.4 GHz only |
| Collector: "Saving clips failed / Failed to fetch" | `app.py` stopped, restart it; unsaved boxes are still in the browser |

---

## 7. Repository layout

```
voicestrip/
├── labels.json            # the 12 classes: key, Estonian name, UI colour (shared by collector + trainer)
├── esp32/voicestrip/      # firmware (Arduino): voice control or collector microphone
│   ├── voicestrip.ino     #   setup/loop, command → light mapping
│   ├── settings.h         #   all options with defaults (override in config.h)
│   ├── audio.*  leds.*    #   microphone capture, LED PWM + effects
│   ├── kws.*              #   log-mel + TFLite Micro keyword spotting
│   ├── collector.*        #   WiFi streaming to the collector
│   └── features.h, model_data.h, test_clip.h   # generated by trainer/export.py
├── models/                # published model: voicestrip.pt (PyTorch) + voicestrip.tflite
├── Data/                  # collector web app (Flask): record, label, cut clips
└── trainer/               # PyTorch training, evaluation, export
```

## License

TODO

Author: Märten Pall

# voicestrip

Häälega juhitav RGB LED riba: masinaõppe mudel tunneb ära eestikeelse käskluse ("sisse", "punane", "välja", …)
ja LED riba reageerib (süttib, vahetab värvi, kustub).

Detailsed juhendid: andmete koguja → [`Data/README.md`](Data/README.md), treener → [`trainer/README.md`](trainer/README.md).

---

## Mida projekt teeb

Kasutaja ütleb käskluse → mikrokontroller tuvastab selle → RGB LED riba reageerib.

**Lõpp-süsteem (eesmärk):**
- ESP32-S3 + INMP441 I2S mikrofon + 3× MOSFET + 12 V LED riba
- üks seinapistik, iseseisev kast
- mudel jookseb ESP32-l (TFLite Micro)

**Praegune arendus-setup:**
- **Andmete kogumine:** Flask koguja (`Data/`) kolme heliallikaga: brauseri mikrofon, Raspberry Pi INMP441,
  ESP32-S3 INMP441 üle WiFi
- **Treenimine:** PyTorch torustik (`trainer/`) ML masinal (Ubuntu + GTX 1650)
- **Tuvastus:** vahepealne `inference.py` Raspberry Pi 3 peal, kuni ESP32 inference sketch on valmis

---

## Kataloogi struktuur

```
voicestrip/
├── Data/                        # andmete koguja (Flask, ühe kasutaja, ilma sisselogimiseta)
│   ├── app.py                   # lehed + API: salvestus, Pi mic, ESP32 voog, klippide lõikamine, CSV
│   ├── pi_mic.py                # INMP441 Pi'l: sounddevice, 48 kHz int32 → 16 kHz mono
│   ├── esp32_link.py            # ESP32 WiFi voo olek (poll/chunk protokoll, ilma Flaskita)
│   ├── templates/               # recorder.html (ka /review/<id>), playback.html, mic_test.html, base.html
│   ├── static/                  # recorder.js, playback.js, waveform.js, worklet.js, mic_test.js, ui.js
│   ├── scripts/
│   │   ├── import_drop.py       # Pi salvestuste import arvutisse (id-konfliktide lahendamine)
│   │   └── merge_tundmatu_to_taust.py  # ühekordne migratsioon: vana klass "tundmatu" → "taust"
│   ├── recordings/              # (gitignored) original/, clips/<label>/, labels.csv, recordings.csv
│   └── README.md
├── trainer/                     # PyTorch treenimistorustik
│   ├── train.py                 # treening (seaded: utils/config.py, muudatused: -o key=value)
│   ├── eval.py                  # hindamine kontrollpunkti pealt
│   ├── engine.py, labels.py
│   ├── utils/config.py          # KÕIK seaded dataclass'idena (andmed, tunnused, mudel, treening)
│   ├── models/                  # my_model.py (CNN), registry.py, configs/
│   ├── features/extractors.py   # log-mel / MFCC (GPU-l)
│   ├── data/                    # dataset.py, augment.py (nihe, helitugevus, taustamüra), splits.py
│   ├── runs/                    # (gitignored) iga treeningu väljund
│   ├── best.pt                  # (gitignored) mudel, mida inference.py kasutab
│   └── README.md
├── esp32/
│   ├── collector/               # ESP32 WiFi mikrofon koguja jaoks
│   │   ├── collector.ino
│   │   └── config.example.h     # → kopeeri config.h-ks (WiFi + serveri IP; gitignored)
│   └── model.h                  # eksporditud TFLite mudel C-massiivina (4 klassi), sketch puudub veel
├── inference.py                 # Pi reaalajas tuvastus + LED juhtimine (vahepealne)
└── README.md
```

`main.py` on PyCharmi näidisfail ja `pyproject.toml` / `uv.lock` on tühja sõltuvusnimekirjaga — sõltuvused on
`Data/requirements.txt` ja `trainer/requirements.txt` failides.

---

## Riistvara

### Komponendid

| Komponent | Mudel | Mille jaoks |
|---|---|---|
| Mikrokontroller | ESP32-S3-DevKitC-1 N16R8 (16 MB flash, 8 MB OPI PSRAM) | lõpp-seade |
| Mikrofon | INMP441 I2S MEMS | hääle sisend |
| MOSFETid | 3× IRFZ44N | LED riba värvikanalite lülitamine |
| Level shifterid | 3× S9013 NPN | 3.3 V GPIO → 12 V MOSFET gate |
| Takistid | 3× 1 kΩ (baas), 3× 10 kΩ (gate pull-up 12 V-le) | |
| Toitemoodul | LM2596HV | 12 V → 5 V ESP32 toide |
| LED riba | 5 m 5050 RGB 12 V (non-addressable) | valgus |
| Toide | 12 V DC adapter (vt voolutarve allpool) | põhitoide |
| Vahepealne | Raspberry Pi 3 + teine INMP441 | kogumine + tuvastus kuni ESP32 valmib |

### ESP32-S3 pinid

| Signaal | GPIO | Märkus |
|---|---|---|
| INMP441 SCK | 4 | |
| INMP441 WS | 3 | |
| INMP441 SD | 2 | |
| INMP441 VDD / GND | 3.3V / GND | |
| INMP441 L/R | GND | vasak kanal (`I2S_MIC_SLOT I2S_STD_SLOT_LEFT`) |
| LED R | 9 | läbi S9013 → IRFZ44N |
| LED G | 10 | läbi S9013 → IRFZ44N |
| LED B | 11 | läbi S9013 → IRFZ44N |
| 5V | LM2596 OUT+ | |

Raspberry Pi peal (`inference.py`): LED R = GPIO 26, G = GPIO 12, B = GPIO 13; INMP441 standardsetel
I2S pinidel (BCLK GPIO 18, LRCLK GPIO 19, DIN GPIO 20).

### Vooluring

```
12 V PSU ─┬─ LED riba +12 V (otse)
          ├─ 10 kΩ ─ MOSFET gate (×3)       ← gate on vaikimisi 12 V-l = LED PÕLEB
          └─ LM2596 IN+ → OUT+ 5 V → ESP32 5V

ESP32 GPIO ─ 1 kΩ ─ S9013 baas;  S9013 kollektor → MOSFET gate;  emitter → GND
MOSFET drain → LED riba R/G/B;   source → GND

Ühine GND: PSU−, LM2596 IN−/OUT−, ESP32 GND, MOSFET source'id, S9013 emitterid
```

### ⚠️ Pööratud loogika

NPN level shifter pöörab signaali ümber: **GPIO HIGH → NPN juhib → gate tõmmatakse GND-le → LED kustu**,
**GPIO LOW → LED põleb**. Seepärast:
- ESP32 sketchides `LED_ACTIVE_LOW 1` (collector.ino vaikimisi), Pi'l `inference.py` kirjutab `(255 - r) / 255`;
- vale seade paneb kogu riba **täisvalgesse**;
- kuni ESP32 käivitub või sketchi laetakse üles (GPIO-d on ujuvad), on NPN-id kinni ja **riba põleb täies
  heleduses**. Selle vältimiseks: 10 kΩ GPIO → 3.3 V pull-up iga kanali GPIO poolele (NPN juhib → LED kustu,
  kuni sketch pinid üle võtab).

### ⚡ Voolutarve

5 m 5050 RGB riba (60 LED/m) võtab täisvalges umbes **1.2 A/m → ~6 A (72 W)**. 2 A adapter ei suuda seda —
see läheb ülekoormuskaitsesse ja hakkab välja-sisse lülituma (adapteri LED vilgub, ESP32 taaskäivitub).
Juhtub täisvalges (`sisse` = 255, 255, 255) ja ka käivitamisel ülaltoodud pööratud loogika tõttu.
Lahendus: **12 V ≥ 6 A adapter** või piira firmware'is maksimaalset heledust.

---

## Tarkvara stack

| Kiht | Tehnoloogia |
|---|---|
| Andmete kogumine (UI) | Flask + vanilla JS + Tailwind CDN, failid + CSV (andmebaasi pole) |
| Audio I/O | Web Audio API (brauser), sounddevice (Pi), ESP-IDF `i2s_std` (ESP32) |
| ESP32 → server | HTTP POST ~0.25 s PCM tükkidena, tekstikäsud `idle` / `stream <id>` / `stop <id>` |
| ML treenimine | PyTorch + torchaudio |
| ML export | ai-edge-torch / litert-torch → TFLite (skript pole veel repos) |
| ML inference | PyTorch (Pi), TFLite Micro (ESP32, plaanis) |
| ESP32 firmware | Arduino IDE + "esp32" by Espressif **3.x** (testitud 3.3.12) |

### Python versioonid

- **Trainer:** Python 3.12 (pyenv), sest ai-edge-torch ei toeta 3.14
- **Koguja Windowsis:** Python 3.14 venv (`Data/venv`)
- **Koguja/inference Pi'l:** süsteemi Python (Pi OS), venv `--system-site-packages`-iga

---

## Setup — nullist töötavasse süsteemi

### 1. Koguja (Flask)

**Windows / sülearvuti:**
```powershell
cd Data
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py --host 0.0.0.0
```

- Ava `http://localhost:5000` — brauseri mikrofon töötab ainult `localhost`-i või HTTPS-i kaudu.
- `--host 0.0.0.0` on vajalik, et ESP32 serverini jõuaks. Käivitusel prinditakse rida
  `ESP32 mikrofon: config.h -> SERVER_HOST "<ip>"` — see IP läheb ESP32 `config.h`-sse.
- Windowsi tulemüür: luba Python (kui Wi-Fi profiil on *Public*, siis Public võrgus).
- Arvuti IP võib ruuteris muutuda → seo see ruuteri DHCP seadetes püsivaks (DHCP reservation).

**Raspberry Pi (INMP441):**
```bash
# /boot/firmware/config.txt:  dtparam=i2s=on  +  dtoverlay=googlevoicehat-soundcard
sudo apt install -y libportaudio2 python3-sounddevice python3-scipy python3-numpy
cd ~/voicestrip/Data
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
python app.py --host 0.0.0.0
```

Ava arvutist `http://<pi-ip>:5000`. Mikrofoni kontroll: `/pi-mic-test`. Detailid: [`Data/README.md`](Data/README.md).

### 2. ESP32 WiFi mikrofon (koguja jaoks)

1. Arduino IDE → Boards Manager → **esp32 by Espressif 3.x**
2. **File → Open → `esp32/collector/collector.ino`** — akna pealkiri peab olema `collector`
   (IDE avab iga sketchi eraldi aknas; vale aknast üleslaadimine laeb vana sketchi).
3. Kopeeri `config.example.h` → `config.h`, täida `WIFI_SSID`, `WIFI_PASSWORD` (2.4 GHz), `SERVER_HOST`, `SERVER_PORT 5000`.
4. **Tools:**

   | Seade | Väärtus |
   |---|---|
   | Board | ESP32S3 Dev Module |
   | USB CDC On Boot | **Disabled**, kui kaabel on "COM" pordis (CH343, nt COM12); **Enabled**, kui native "USB" pordis |
   | PSRAM | OPI PSRAM (N16R8) |
   | Flash Size | 16MB |
   | Upload Speed | 921600 |

5. **Ctrl+U** — väljundis peab olema `collector.ino.bin`.
6. Serial Monitor 115200 → `Server http://…:5000 vastab - valmis salvestama`, LED roheline.
7. Kontroll brauseris: `/esp32-mic-test`.

### 3. Trainer (ML masin, GTX 1650)

```bash
pyenv install 3.12
cd trainer
pyenv local 3.12
python -m venv venv
source venv/bin/activate
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
python -c "import torch; print(torch.cuda.is_available())"   # peab olema True
```

Andmed loetakse kohast `../Data/recordings/` (`utils/config.py` → `DataConfig`).

### 4. Mudel ESP32-le (TODO)

`esp32/model.h` on olemas (4 klassi: `kone`, `sisse`, `taust`, `valja`; 155 580 baiti), aga eksportskript
ja ESP32 inference sketch pole veel repos. Plaan: `best.pt` → ai-edge-torch/litert-torch → `.tflite` →
C-massiiv `model.h` → TFLite Micro sketch (Arduino IDE: Partition Scheme *16M Flash (3MB APP/9.9MB FATFS)*).

---

## Workflow — andmetest mudelini

### A. Andmete kogumine

1. Käivita koguja (`python app.py --host 0.0.0.0`) ja ava `http://localhost:5000`.
2. Vali **Allikas**: brauseri mikrofon / Pi I2S / **ESP32 mikrofon** (roheline täpp = võrgus).
3. Vali vaikimisi silt ja nimi → **RECORD** (või tühik) → ütle käsklusi → **STOP**.
4. Originaal-WAV salvestatakse kohe (`recordings/original/`) ja samal lehel avaneb lainekuju-redaktor.
5. Paiguta 1 s kastid (`B` = kast playheadi kohale), vali igale silt → **💾 Accept & save all**.
6. Klipid → `recordings/clips/<label>/<clip_id>.wav`, read → `recordings/labels.csv` (veerg `source`:
   `browser` / `pi_i2s` / `esp32`).
7. Hiljem jätkamiseks: **Jätka hiljuti salvestust** või `/review/<recording_id>`. Ülevaade: `/playback`.

Kõik helifailid: 16 kHz, 16-bit, mono WAV.

### B. Andmete liigutamine

Ära kopeeri Pi `recordings/` kausta otse arvuti oma peale — mõlemad annavad samal päeval samu id-sid
(`20261120_001`) ja kirjutaksid üksteist üle. Kasuta importi:

```powershell
# kopeeri Pi kaust eraldi kohta, nt C:\incoming\recordings, siis (koguja peatatud):
venv\Scripts\python.exe scripts\import_drop.py --from C:\incoming\recordings          # kuivalt
venv\Scripts\python.exe scripts\import_drop.py --from C:\incoming\recordings --apply
```

ML masinasse viimiseks kopeeri kogu `Data/recordings/` kaust (`labels.csv` + `clips/`).

### C. Treenimine

```bash
cd trainer
source venv/bin/activate
python train.py                                              # seaded utils/config.py-st
python train.py -o training.epochs=2                          # kiire suitsutest
python train.py -o "data.classes=[kone, sisse, taust, valja]" -o data.split_by=recording
```

Praegused vaikeväärtused (`utils/config.py`): klassid `kone, sisse, taust, valja`, `split_by: overlap`,
log-mel 40 mel-riba, klasside kaalumine sees. Ausaks "uue sessiooni" testiks kasuta `split_by: recording`.

Väljund `runs/<run_name>_<YYYYMMDD_HHMMSS>/`: `best.pt`, `last.pt`, `config.yaml`, `splits.json`,
`metrics.csv`, `confusion_matrix.png`, `training_log.txt`.

```bash
python eval.py --checkpoint runs/<run>/best.pt                # sama testijaotus
python eval.py --checkpoint runs/<run>/best.pt --split all
```

### D. Tuvastus Pi'l (vahepealne)

```bash
cp trainer/runs/<run>/best.pt trainer/best.pt
python inference.py
```

Kuulab 1 s aknaid (ilma kattuvuseta), käivitab käskluse kui kindlus ≥ 0.35: `punane/roheline/sinine/valge`
→ värv, `sisse` → valge, `valja` → kustu, `kone/taust` → ei midagi.

### E. Export ESP32-le — TODO (vt Setup 4)

---

## Klassid

Defineeritud `Data/app.py` → `LABELS` (võti = failinimed ja CSV, UI nimi täpitähtedega).

| Võti | UI | Kirjeldus | Pi `inference.py` |
|---|---|---|---|
| `sisse` | sisse | LED sisse | valge |
| `valja` | välja | LED kustu | kustu |
| `punane` | punane | värv | punane |
| `roheline` | roheline | värv | roheline |
| `sinine` | sinine | värv | sinine |
| `valge` | valge | värv | valge |
| `hajuv` | hajuv | sujuv värvivahetus | — |
| `vilkuv` | vilkuv | vilkumine | — |
| `heledam` | heledam | +heledus | — |
| `tumedam` | tumedam | −heledus | — |
| `taust` | taust | vaikus, müra, muusika, uksed… (mitte käsklus) | ei midagi |
| `kone` | kõne | tavaline rääkimine (mitte käsklus) | ei midagi |

### Kui palju andmeid on vaja

Rusikareegel väikese CNN-i jaoks: **≥ 150 klippi märksõna kohta (hea: 300+)** vähemalt 15–20 erinevast
salvestusest, mitmelt inimeselt, eri kaugustelt ja ruumidest. `taust` ja `kone` peaksid olema suurimad klassid
(500–1000+), muidu tekib palju valepositiivseid. Vähemalt pool andmetest peaks tulema seadmest, millel mudel
lõpuks jookseb (ESP32 mikrofon).

---

## Edge Impulse — alternatiivne tee

Kui PyTorch → TFLite eksport jääb toppama (nt STFT kompleksarvude ONNX piirang):

1. Laadi WAV-id üles: edgeimpulse.com → Data acquisition (klipid on juba `clips/<label>/` kaustades — silt kaustanime järgi)
2. Impulse design → MFE block + Classification (Keras)
3. Treeni
4. Deployment → Arduino library → .zip
5. Arduino IDE → Sketch → Include Library → Add .ZIP Library → kasuta näidissketchi

MFE parameetrid: frame 25 ms, stride 10 ms, 40 filtrit, FFT 512.

---

## Troubleshooting

### Koguja
- **"Failed to fetch" / "Saving clips failed":** server ei tööta (terminal suleti) → käivita uuesti. Originaal
  on kettal alles; salvestamata kastid on veel brauseris → vajuta Accept uuesti.
- **Brauseri mikrofon puudub:** ava `http://localhost:5000`, mitte IP kaudu.
- **`labels.csv` Excelis lahti:** Excel lukustab faili → Accept ebaõnnestub. Ava CSV ainult koopiana.

### Pi
- **Undervoltage (`vcgencmd get_throttled` ≠ `0x0`):** tugevam toide (5 V 2.5 A ametlik adapter)
- **I2S mikrofoni pole (`arecord -l` tühi):** kontrolli `/boot/firmware/config.txt`: `dtparam=i2s=on` + `dtoverlay=googlevoicehat-soundcard`;
  käsitsi seadme valik: `PI_MIC_DEVICE=<nr või nimi> python app.py`
- **`.local` nimi ei lahendu:** kasuta IP-d või paigalda `avahi-daemon`

### ESP32
- **Upload "Failed to connect":** hoia BOOT, vajuta RESET, vabasta BOOT → upload
- **Serial Monitor tühi pärast `entry 0x403c88b8`:** USB CDC On Boot ei klapi pordiga (COM/CH343 → Disabled)
  või laeti üles vale sketch (kontrolli `.ino.bin` nime üleslaadimise väljundis)
- **Serial müra:** 115200 baud
- **LED vilgub siniselt:** ei saa WiFi-ga ühendust — SSID/parool `config.h`-s, ainult 2.4 GHz
- **LED vilgub kollaselt / "connection refused":** vale `SERVER_HOST` (arvuti IP muutus?), app.py pole
  `--host 0.0.0.0`-ga käivitatud või tulemüür blokeerib
- **"ESP32 puhver sai täis":** nõrk WiFi, vii ruuterile lähemale (testilehel signaal; alla −75 dBm on nõrk)

### LED-id ja toide
- **Kogu riba põleb täisvalges, adapter vilgub:** pööratud loogika — `LED_ACTIVE_LOW 1` (vt Riistvara) ja/või
  adapter liiga nõrk (vt Voolutarve)
- **Valged kogu aeg:** MOSFET metallplaat puutub teise komponendi vastu → isoleeri
- **Vales järjekorras värvid:** vaheta pinid (`PIN_LED_R/G/B` `config.h`-s, Pi'l `inference.py` `PWMLED(...)`)

### Trainer
- **`aten::stft` ONNX viga:** kasuta ai-edge-torch / litert-torch otse, mitte ONNX vaheetappi
- **`torch.int1` AttributeError:** PyTorch versioon (vajab 2.11+)
- **Python 3.14 ja PyTorch / ai-edge-torch:** pyenv install 3.12, uus venv
- **`NotImplementedError: Build your model…`:** `models/my_model.py` on täitmata

---

## Praegune staatus

- [x] Flask koguja: brauser + Pi I2S + ESP32 WiFi mikrofon, review, playback, Pi import
- [x] PyTorch treenimistorustik (augmentatsioon, lekkekindel jaotus, eval)
- [x] Pi inference (vahepealne, `inference.py`)
- [x] ESP32 + perfboard füüsiline setup
- [x] ESP32 WiFi recorder (`esp32/collector/`) — riistvaral testitud
- [ ] Rohkem andmeid (märksõnad 40–75 klippi/klass → eesmärk 300+, `taust`/`kone` ESP32-st)
- [ ] TFLite eksportskript repo'sse (`model.h` 4 klassiga juba olemas)
- [ ] ESP32 TFLite Micro inference sketch + LED juhtimine
- [ ] Toide: ≥ 6 A adapter või heleduse piirang; GPIO pull-upid käivitusvälgu vastu
- [ ] 3D-prinditud kast

---

Autor: Märten Pall

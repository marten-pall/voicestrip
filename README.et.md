# voicestrip

**Häälega juhitav RGB LED riba.** Ütle *„sisse"*, *„punane"* või *„välja"* ja riba reageerib.
Kõnet tuvastab **ESP32-S3 ise** väikese närvivõrguga: pilve, WiFi-t ega telefonirakendust pole vaja.

🇬🇧 [In English: README.md](README.md)

- **Ehita endale:** osta osad, joota plaat kokku, laadi üles firmware koos avaldatud mudeliga.
  Treenida pole vaja.
- **Tee omaks:** salvesta oma hääl (või uued sõnad) brauseripõhise kogujaga, treeni CNN
  PyTorchis ja ekspordi see ESP32-le.

> **Staatus:** riistvara, firmware, koguja, treenimine ja eksport töötavad. Lõplik treenitud mudel
> (`models/voicestrip.pt` + `esp32/voicestrip/model_data.h`) avaldatakse, kui andmeid on piisavalt.
> Seni treeni oma mudel (peatükk 4).

---

## Sisukord

1. [Käsklused](#1-käsklused)
2. [Komponendid](#2-komponendid)
3. [Kokkupanek ja flashimine](#3-kokkupanek-ja-flashimine)
4. [Oma mudeli treenimine](#4-oma-mudeli-treenimine)
5. [Kuidas see töötab](#5-kuidas-see-töötab)
6. [Tõrkeotsing](#6-tõrkeotsing)
7. [Repo ülesehitus](#7-repo-ülesehitus)

---

## 1. Käsklused

| Ütle | Riba |
|---|---|
| **sisse** | süttib (viimane värv, alguses valge) |
| **välja** | kustub |
| **punane** / **roheline** / **sinine** / **valge** | vastav värv |
| **hajuv** | aeglane vikerkaare värvivahetus |
| **vilkuv** | praegune värv vilgub |
| **heledam** / **tumedam** | samm heledamaks / tumedamaks |

Lisaks on mudelil kaks klassi, mis ei ole käsklused: `taust` (taustamüra) ja `kone` (tavaline
rääkimine). Nende abil oskab riba kõike muud ignoreerida. Avaldatud mudel tunneb ainult klasse,
millega see treeniti (need prinditakse käivitusel Serial Monitori).

---

## 2. Komponendid

| tk | Komponent | Näide | Märkus |
|---|---|---|---|
| 1 | Mikrokontroller | **ESP32-S3-DevKitC-1 N16R8** | 16 MB flash, **8 MB PSRAM on vajalik** (mudel jookseb PSRAM-is) |
| 1 | I2S MEMS mikrofon | **INMP441** moodul | |
| 1 | LED riba | 12 V **mitte-adresseeritav** 5050 RGB (4 juhet: +12V, R, G, B) | siin 5 m |
| 3 | N-kanaliga MOSFET | IRFZ44N | üks iga värvi kohta |
| 3 | NPN transistor | S9013 (või 2N2222, BC337) | nivoonihutaja: 3.3 V GPIO → 12 V gate |
| 3 | Takisti 1 kΩ | | NPN baas |
| 3 | Takisti 10 kΩ | | MOSFET-i gate pull-up 12 V-le |
| 3 | Takisti 10 kΩ | *soovituslik* | GPIO pull-up 3.3 V-le: hoiab riba ESP32 käivitumise ajal **kustu** |
| 1 | Pingemuundur | LM2596HV moodul | 12 V → 5 V ESP32-le |
| 1 | Toiteplokk | **12 V, ≥ 6 A** 5 m riba jaoks | vt *Toide* allpool |
| | Perfboard, juhtmed, DC pesa, klemmid | | soovi korral 3D-prinditud kast |

### Toide

5 m 5050 RGB riba (60 LED/m) võtab täisvalges **umbes 1.2 A meetri kohta ehk ~6 A (72 W)**.
Nõrgem adapter läheb ülekoormuskaitsesse, lülitub välja ja uuesti sisse (selle LED vilgub) ning
ESP32 taaskäivitub pidevalt. Kasuta 12 V ≥ 6 A adapterit või piira heledust `config.h`-s:
2 A adapteri puhul `#define LED_MAX_BRIGHTNESS 80`.

### Ühendused

**ESP32-S3**

| Signaal | GPIO | |
|---|---|---|
| INMP441 SCK | 4 | |
| INMP441 WS | 3 | |
| INMP441 SD | 2 | |
| INMP441 VDD / GND | 3V3 / GND | |
| INMP441 L/R | GND | vasak kanal |
| LED punane | 9 | 1 kΩ kaudu NPN baasile |
| LED roheline | 10 | 1 kΩ kaudu NPN baasile |
| LED sinine | 11 | 1 kΩ kaudu NPN baasile |
| 5V | LM2596 OUT+ | **sea LM2596 väljund 5.0 V peale enne ESP32 ühendamist** |
| GND | ühine maa | |

**Üks värvikanal (ehita kolm korda):**

```
                         +12 V
                           │
                         10 kΩ (gate pull-up)
                           │
GPIO ──┬── 1 kΩ ──► S9013 baas     S9013 kollektor ──┬── IRFZ44N gate
       │                           S9013 emitter ── GND
     10 kΩ (soovituslik,                             IRFZ44N drain ── LED riba R / G / B
       │    3.3 V-le)                                IRFZ44N source ── GND
     3.3 V

LED riba +12 V ── toiteploki +12 V (otse)
```

NPN aste **pöörab signaali ümber**: GPIO HIGH → transistor juhib → gate tõmmatakse GND-le → LED
**kustu**; GPIO LOW → LED põleb. Firmware arvestab sellega (`LED_ACTIVE_LOW 1`, vaikimisi). Kui
juhid MOSFET-i gate'e otse, pane `#define LED_ACTIVE_LOW 0`.
Ilma soovituslike 10 kΩ GPIO pull-upideta süttib riba igal käivitusel ja üleslaadimisel **hetkeks
täisvalgesse** (GPIO-d on ujuvad, kuni firmware käivitub).

**Ühine maa:** toiteploki −, LM2596 IN−/OUT−, ESP32 GND, MOSFET-ide source'id ja NPN emitterid.

---

## 3. Kokkupanek ja flashimine

1. Paigalda **[Arduino IDE 2](https://www.arduino.cc/en/software)**, siis *Tools → Board → Boards
   Manager*: paigalda **„esp32" by Espressif Systems, versioon 3.x** (testitud: 3.3.12). See sisaldab
   juba TensorFlow Lite Microt ja esp-dsp-d, lisateeke pole vaja.
2. Klooni või laadi alla see repo, siis *File → Open →* `esp32/voicestrip/voicestrip.ino`.
   Akna pealkiri peab olema **voicestrip** (iga sketch avaneb eraldi aknas).
3. *Tools* menüü:

   | Seade | Väärtus |
   |---|---|
   | Board | ESP32S3 Dev Module |
   | PSRAM | **OPI PSRAM** |
   | Flash Size | 16MB (128Mb) |
   | Partition Scheme | 16M Flash (3MB APP/9.9MB FATFS) |
   | USB CDC On Boot | **Disabled**, kui kaabel on **COM/UART** pordis (CH340/CH343 kiip); **Enabled**, kui natiivses **USB** pordis |
   | Port | COM port, mis tekib plaadi ühendamisel |

4. Valikulised seaded käivad `config.h`-sse: kopeeri `config.example.h` → `config.h`
   (heleduse piirang, lävi, pinid, …).
5. **Upload** (Ctrl+U). Väljundis peab olema `voicestrip.ino.bin`. Kui tuleb *„Failed to connect"*:
   hoia **BOOT**, vajuta ja vabasta **RESET**, vabasta **BOOT**, lae uuesti.
6. *Tools → Serial Monitor*, **115200** baud, vajuta **RESET**:

   ```
   voicestrip voicestrip-2.0
   Helipuhver: 32.8 s (PSRAM)
   Režiim: häälkäsklused, klassid: hajuv heledam kone punane ... | lävi 0.80
   Mudel: … baiti, int8, arena … / 1536 KB
   Kuulan. Ütle nt "sisse".
   ```

   Lühike roheline välgatus = kuulab. Ütle **„sisse"**.

**Häälestamine** (`config.h`): `KWS_THRESHOLD` (vaikimisi 0.80, suurem = vähem valekäivitusi),
`KWS_DEBUG 1` prindib iga ennustuse, `KWS_SELFTEST 1` kontrollib käivitusel, kas ESP32 arvutab
samad tunnused ja väljundi nagu arvuti (`test_clip.h`).

---

## 4. Oma mudeli treenimine

Selle repo mudel õppis *ühe* inimese häält *ühes* kodus. See töötab kõige paremini sarnaste häälte
ja ruumidega. Oma hääle, aktsendi või uute sõnade jaoks kogu andmed ja treeni. Selleks kulub õhtu
või kaks.

### 4.1 Andmete kogumine

**Koguja** (`Data/`) on väike Flaski veebirakendus. Salvestad pika võtte, paigutad lainekujule iga
sõna kohale 1-sekundilise kasti ja koguja lõikab neist sildistatud 16 kHz WAV klipid ning
`labels.csv` faili. Salvesta **ESP32 enda mikrofoniga**, sest mudel töötab kõige paremini sama
mikrofoniga, millega see hiljem kuulab.

```bash
cd Data
python -m venv venv
venv\Scripts\activate            # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python app.py --host 0.0.0.0
```

Rakendus prindib selle arvuti kohtvõrgu IP. Edasi:

1. ESP32 WiFi mikrofoniks: kirjuta `esp32/voicestrip/config.h`-sse

   ```cpp
   #define COLLECTOR_MODE 1
   #define WIFI_SSID     "sinu-2.4GHz-wifi"
   #define WIFI_PASSWORD "..."
   #define SERVER_HOST   "192.168.1.23"   // IP, mille app.py prindis
   ```

   ja lae üles. LED-id: sinine vilkumine = ühendab WiFi-ga, kollane vilkumine = server ei vasta,
   roheline = valmis, punane = salvestab.
2. Ava `http://localhost:5000` ja vali **Allikas: ESP32 mikrofon** (roheline täpp = võrgus).
   Kontrolli enne taset lehel `/esp32-mic-test`.
3. **RECORD** → ütle sõnad pausidega → **STOP** → paiguta kastid (`B` klahv) → vali igale kastile
   silt → **💾 Accept**. Töötab ka brauseri mikrofon (ainult `localhost`-i kaudu).

Windowsis luba Python tulemüürist läbi (*Private* võrgus, või *Public*, kui su WiFi profiil on
avalik). Detailid ja CSV formaat: [`Data/README.md`](Data/README.md).

**Kui palju andmeid on vaja:** vähemalt ~150 klippi käskluse kohta (hea: 300+), **15+ eraldi
salvestusest**, mitmelt inimeselt, eri kaugustelt ja ruumidest. `taust` ja `kone` peaksid olema
suurimad klassid (500–1000+): pikad salvestused vaikusest, muusikast, telekast ja tavalisest
jutust. Muidu reageerib riba tavalisele kõnele.

### 4.2 Treenimine

Masinas, kus on PyTorch (GPU aitab, CPU töötab ka):

```bash
cd trainer
python -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate
pip install torch torchaudio     # CUDA versioon: vt pytorch.org
pip install -r requirements.txt
python train.py -o "data.classes=[hajuv, heledam, kone, punane, roheline, sinine, sisse, taust, tumedam, valge, valja, vilkuv]" -o data.split_by=recording
```

Kõik seaded on failis `trainer/utils/config.py`; `-o key=value` muudab ühte neist. Iga treening
kirjutab kausta `runs/<nimi>_<aeg>/` faili `best.pt`, mõõdikud ja segadusmaatriksi.
`split_by=recording` hoiab terved salvestused treeningust eemal, et testitulemus oleks aus.
Detailid: [`trainer/README.md`](trainer/README.md).

### 4.3 Eksport ESP32-le

TFLite konverter vajab **Linuxit või WSL-i Python 3.12-ga**:

```bash
pip install litert-torch
python export.py --checkpoint runs/<run>/best.pt
```

See kirjutab failid `esp32/voicestrip/features.h`, `model_data.h`, `test_clip.h` ja
`models/voicestrip.{pt,tflite}`. Enne kirjutamist kontrollitakse, et

- firmware tunnuste kood annab samad numbrid nagu treening (max erinevus ~1e-6) ja
- int8 TFLite mudel ennustab testijaotusel sama mis PyTorch (mõlema täpsus prinditakse).

Seejärel pane `config.h`-s `COLLECTOR_MODE 0` (või kustuta see rida) ja lae uuesti üles
(peatükk 3). `python export.py ... --features-only` töötab ka Windowsis (ilma TFLite'ita), et
tunnuseid kontrollida.

---

## 5. Kuidas see töötab

```
INMP441 ──I2S 16 kHz──► ringpuhver ──iga 250 ms: viimane 1 s──► log-mel 40×97 ──► CNN (int8, TFLite Micro)
                                                                                         │
               LED riba ◄── PWM ◄── käsklus ◄── 3 keskmine ≥ 0.80, 1.5 s paus ◄──────────┘
```

- **Tunnused:** 25 ms Hanni aken, 10 ms samm, 512-punktiline FFT, 40 mel-riba, logaritm,
  normaliseerimine riba kaupa. Treener (torchaudio) ja firmware (esp-dsp) arvutavad neid
  identselt; `export.py` genereerib konstandid ja kontrollib seda.
- **Mudel:** 2 × (Conv 3×3 → BatchNorm → ReLU), max-pool, globaalne keskmine, lineaarkiht.
  Treenitud ajanihke, helitugevuse ja taustamüra augmentatsiooni ning SpecAugmendiga.
- **Otsused:** ennustus iga 250 ms järel libiseval 1 s aknal; kolm viimast keskmistatakse;
  käsklus vajab ≥ 0.80 ja seejärel peab enne järgmist mööduma 1.5 s.

---

## 6. Tõrkeotsing

| Sümptom | Lahendus |
|---|---|
| Kogu riba täisvalges, adapteri LED vilgub | Vale loogikatase: `LED_ACTIVE_LOW` (peatükk 2) ja/või liiga nõrk adapter: `LED_MAX_BRIGHTNESS` |
| Riba välgatab käivitusel / üleslaadimisel valgelt | Lisa 10 kΩ GPIO → 3.3 V pull-upid |
| Serial Monitoris ainult `entry 0x403c88b8` | *USB CDC On Boot* ei klapi kasutatava USB pordiga või laeti üles vale sketch (kontrolli `.ino.bin` nime) |
| `Failed to connect to ESP32-S3` | BOOT + RESET (peatükk 3, samm 5) |
| Kompileerimisel `Voice control needs model_data.h` | Mudelit pole veel eksporditud: ekspordi (4.3) või kasuta `COLLECTOR_MODE 1` |
| `Mudeli mälu ... eraldamine ebaõnnestus` | *Tools → PSRAM → OPI PSRAM* |
| `Didn't find op for builtin opcode` | Lisa see op `kws.cpp` resolverisse (`resolver.Add...`) |
| Reageerib tavalisele jutule / telekale | Tõsta `KWS_THRESHOLD`; salvesta rohkem `kone` ja `taust` klippe |
| Ei reageeri kunagi | `KWS_DEBUG 1` ja jälgi tõenäosusi; kontrolli mikrofoni taset koguja režiimis lehel `/esp32-mic-test` |
| Mikrofoni tase alati täpselt 0 | SD/WS/SCK juhtmed; L/R GND-le või `#define I2S_MIC_SLOT I2S_STD_SLOT_RIGHT` |
| Koguja: ESP32 täpp jääb halliks, LED vilgub kollaselt | Vale `SERVER_HOST` (arvuti IP muutus?), `app.py` ilma `--host 0.0.0.0`-ta või tulemüür |
| Koguja: LED vilgub siniselt | WiFi nimi/parool, ainult 2.4 GHz |
| Koguja: „Saving clips failed / Failed to fetch" | `app.py` seiskus, käivita uuesti; salvestamata kastid on veel brauseris |

---

## 7. Repo ülesehitus

```
voicestrip/
├── labels.json            # 12 klassi: võti, eestikeelne nimi, UI värv (ühine kogujale ja treenerile)
├── esp32/voicestrip/      # firmware (Arduino): häälkäsklused või koguja mikrofon
│   ├── voicestrip.ino     #   setup/loop, käsklus → valgus
│   ├── settings.h         #   kõik seaded vaikeväärtustega (muuda config.h-s)
│   ├── audio.*  leds.*    #   mikrofoni lugemine, LED PWM + efektid
│   ├── kws.*              #   log-mel + TFLite Micro märksõnatuvastus
│   ├── collector.*        #   WiFi voog kogujasse
│   └── features.h, model_data.h, test_clip.h   # genereerib trainer/export.py
├── models/                # avaldatud mudel: voicestrip.pt (PyTorch) + voicestrip.tflite
├── Data/                  # koguja veebirakendus (Flask): salvesta, sildista, lõika klipid
└── trainer/               # PyTorchi treenimine, hindamine, eksport
```

## Litsents

TODO

Autor: Märten Pall

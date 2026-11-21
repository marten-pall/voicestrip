# Voice Collector

Hääleandmete koguja voicestripi märksõnamudeli jaoks. Salvestad pikema võtte (ESP32 mikrofon või
brauseri mikrofon), paigutad lainekujule 1-sekundilised kastid sõnade kohale ja koguja lõikab neist
16 kHz / 16-bit / mono WAV klipid koos CSV sildifailiga.

Üks kasutaja, ilma sisselogimiseta, failid + CSV (andmebaasi pole). ESP32 ühendamine ja kogu
projekti töövoog: [juur-README](../README.et.md#41-andmete-kogumine).

## Käivitamine

```powershell
python -m venv venv
venv\Scripts\activate          # Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
python app.py --host 0.0.0.0
```

- Ava `http://localhost:5000`. Brauseri mikrofon töötab ainult `localhost`-i või HTTPS-i kaudu.
- `--host 0.0.0.0` on vajalik ESP32 jaoks. Käivitusel prinditakse rida
  `ESP32 mikrofon: config.h -> SERVER_HOST "<ip>"`, see IP käib ESP32 `config.h`-sse.
- Rakendusel pole parooli ja seal saab salvestusi kustutada: kasuta ainult usaldusväärses võrgus.
- Kui arvuti IP ruuteris muutub, peab ka `SERVER_HOST`-i muutma. Seo arvuti IP ruuteri DHCP
  seadetes püsivaks.

## Kasutamine

1. **Allikas**: *ESP32 mikrofon* (roheline täpp = võrgus) või *Brauseri mikrofon*. Valik jäetakse meelde.
   ESP32 taset ja WiFi signaali näeb lehel `/esp32-mic-test` (link allika kõrval).
2. **Vaikimisi silt** ja salvestuse nimi.
3. **RECORD** (või tühik) → ütle sõnu pausidega → **STOP**. Originaal-WAV salvestatakse kohe kettale.
4. **Kastid**: klõpsa lainekujul (playhead), `B` või `+ Add box`, lohista kast sõna kohale, vali silt
   → **💾 Accept & save all**.

Kiirklahvid: `Space` salvesta/peata või esita/paus, `B` kast playheadi kohale, `Delete` kustuta valitud kast.

Pärast Accepti jäävad salvestatud kastid katkendjoonega ekraanile ja vahedesse saab lisada uusi
(`💾 Salvesta uued boxid`). Varasemaid salvestusi saab jätkata rippmenüüst **Jätka hiljuti
salvestust** või aadressilt `/review/<recording_id>`. **Playback** lehel (`/playback`) on klipid
salvestuste kaupa koos eelvaatega, filtreeritavad sildi ja allika järgi.

Kui ESP32 ühendus salvestamise ajal katkeb (6 s heli ei tule), peatab brauser salvestuse ise ja
säilitab kohale jõudnud osa. Kui leht salvestamise ajal uuesti laadida, jätkab see käimasolevat
ESP32 salvestust.

## Sildid

Defineeritud failis [`../labels.json`](../labels.json) (võti → eestikeelne nimi + UI värv); sama faili loeb
treener. Võti on ASCII (failinimed, CSV, mudeli klassid): `sisse`, `valja`, `punane`, `roheline`, `sinine`,
`valge`, `hajuv`, `vilkuv`, `heledam`, `tumedam`, `taust` (vaikus, ruumimüra, muusika, uksed, köha, koer),
`kone` (tavaline rääkimine, mitte käsklus).

## Failid

```
recordings/                 # (gitignored)
├── original/               # täispikad salvestused: YYYYMMDD_NNN_<nimi>.wav
├── clips/<label>/          # 1.0 s klipid sildi kaupa: <clip_id>.wav
├── labels.csv              # klippide sildid
└── recordings.csv          # originaalsalvestuste nimekiri (nimi, kestus, vaikimisi silt, allikas)
```

Kõik WAV-id: 16-bit PCM, mono, 16 000 Hz.

`labels.csv` (UTF-8 BOM-iga, et Excel kuvaks täpitähed):

| veerg | kirjeldus |
|---|---|
| `clip_id` | `<recording_id>_<N>_<label>`, nt `20261020_001_2_punane` |
| `recording_id` | `YYYYMMDD_NNN` (päevane loendur) |
| `label` | sildi võti |
| `start_sec` | klipi algus originaalis (sekundites) |
| `clip_path` | tee `recordings/` suhtes, nt `clips/punane/….wav` |
| `recording_name` | salvestuse nimi |
| `source` | `browser` või `esp32` (vanemates andmetes ka `pi_i2s`, `upload`) |
| `created_at` | ISO ajatempel |

```python
import csv
from pathlib import Path

with Path("recordings/labels.csv").open(newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        print(row["clip_id"], row["label"], Path("recordings") / row["clip_path"])
```

**Excel:** ära hoia `labels.csv`-d Excelis lahti, kui koguja töötab. Excel lukustab faili ja Accept
ebaõnnestub (koguja teatab sellest ega jäta pooleldi salvestatud klippe maha). Ava fail koopiana või
*Data → From Text/CSV* (UTF-8, eraldaja koma).

## ESP32 protokoll

ESP32 on alati HTTP klient (koodis `esp32_link.py` ja `../esp32/voicestrip/collector.cpp`):

- ootel: `GET /api/esp32/poll` iga 300 ms (ühtlasi elusolek: 3 s vaikust = võrgust väljas);
- salvestamisel: `POST /api/esp32/chunk?stream=<id>&seq=<n>&dropped=<k>&final=<0|1>`, keha = ~0.25 s
  toorest 16 kHz int16 PCM-i;
- iga vastus on üks tekstirida: `idle`, `stream <id>` või `stop <id>`.

STOP-i peale saadab ESP32 puhvrisse jäänud heli (viimane tükk `final=1`) ja server ootab seda kuni paar
sekundit. Korduvaid tükke (kadunud vastuse järel) tuntakse `seq` järgi ära, puhvri ületäitumisest ja
katkestustest antakse hoiatus.

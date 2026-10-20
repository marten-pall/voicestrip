# Voice Collector

## Mis see on

Hääleandmete kogumise tööriist RGB LED kontrolleri märksõnatuvastuse (keyword spotting) mudeli treenimiseks.
Salvestad brauseris pikema heliklipi, paigutad lainekujule 1-sekundilised kastid märksõnade kohale
ja tööriist lõikab neist 16 kHz / 16-bit / mono WAV klipid koos CSV sildifailiga.

Töötab ainult lokaalselt (localhost), ühe kasutajaga, ilma sisselogimiseta. Testitud: Windows 11 + Chrome.

## Setup Windowsis

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Linux/Mac puhul aktiveeri venv nii:

```bash
source venv/bin/activate
```

Ava Chrome: http://localhost:5000

> Kasuta just `localhost`, mitte IP-aadressi — Chrome lubab mikrofoni ainult HTTPS-i või localhosti kaudu.

## Kuidas kasutada

1. **Vali silt** — vali vaikimisi silt (nt `punane`) ja soovi korral salvestuse nimi.
2. **RECORD** — vajuta nuppu või tühikut, ütle märksõnu, peata uuesti tühikuga.
3. **Paiguta boxid → Accept** — klõpsa lainekujul (playhead), vajuta `B` või `+ Add box`, lohista kast
   sõna kohale, vajadusel muuda kasti silti ja vajuta `💾 Accept & save all`.

Kiirklahvid: `Space` — salvesta/peata või esita/paus, `B` — lisa kast playheadi kohale, `Delete` — kustuta valitud kast.

Pärast Accepti jääb salvestus ekraanile: salvestatud kastid muutuvad katkendjoonega poolläbipaistvaks
(neid ei saa enam liigutada ega kustutada) ja vahedesse saab lisada uusi kaste → `💾 Salvesta uued boxid`.
`🎙 Uus salvestus` alustab puhtalt lehelt. Originaal-WAV salvestatakse kettale kohe pärast Stop-i.

Varasemaid salvestusi saab jätkata rippmenüüst **Jätka hiljuti salvestust** (10 viimast) või lehelt
`/review/<recording_id>`. Seal on ka nupp `🗑 Kustuta originaal + kõik klipid` testsalvestuste koristamiseks.

Lehel **Playback** (`/playback`) on klipid grupeeritud salvestuste kaupa: `▶ Preview` näitab originaali
lainekuju koos olemasolevate kastidega, `Jump to review →` avab salvestuse review-režiimis.

## Sildid (13 klassi)

| võti (failid, CSV) | UI nimi    | tähendus                                  |
|--------------------|------------|-------------------------------------------|
| `sisse`            | sisse      | LED sisse                                 |
| `valja`            | välja      | LED kustub                                |
| `punane`           | punane     | värvi valik                               |
| `roheline`         | roheline   | värvi valik                               |
| `sinine`           | sinine     | värvi valik                               |
| `valge`            | valge      | värvi valik                               |
| `hajuv`            | hajuv      | sujuv värvimuutus                         |
| `vilkuv`           | vilkuv     | vilkumine                                 |
| `heledam`          | heledam    | heleduse muutmine                         |
| `tumedam`          | tumedam    | heleduse muutmine                         |
| `taust`            | taust      | vaikus, ruumimüra, muusika                |
| `kone`             | kõne       | tavaline rääkimine, mitte-käsklus kõne    |
| `tundmatu`         | tundmatu   | muud helid (uksed, köha, koer)            |

Sildid on defineeritud `app.py` alguses (`LABELS`, `LABEL_COLORS`); kõik rippmenüüd ja värvid tulevad sealt.

## CSV struktuur

Fail: `recordings/labels.csv` (UTF-8 BOM-iga, et Excel kuvaks täpitähed õigesti).

| veerg            | kirjeldus                                               |
|------------------|---------------------------------------------------------|
| `clip_id`        | `<recording_id>_<N>_<label>`, nt `20261020_001_2_punane` |
| `recording_id`   | `YYYYMMDD_NNN` (päevane loendur)                        |
| `label`          | sildi võti (ASCII): `sisse`, `valja`, `punane`, …       |
| `start_sec`      | klipi algus originaalsalvestuses (sekundites)           |
| `clip_path`      | tee `recordings/` kausta suhtes, nt `clips/punane/….wav` |
| `recording_name` | salvestuse nimi                                         |
| `source`         | `browser` (hiljem `esp32`)                              |
| `created_at`     | ISO ajatempel                                           |

Pythonis:

```python
import csv
from pathlib import Path

with Path("recordings/labels.csv").open(newline="", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        print(row["clip_id"], row["label"], Path("recordings") / row["clip_path"])
```

Pandasega: `pd.read_csv("recordings/labels.csv", encoding="utf-8-sig")`.

Excelis: **Data → From Text/CSV**, vali kodeering *UTF-8* ja eraldaja *koma*
(eesti lokaadiga Excel eeldab topeltklõpsul semikoolonit, seega import on kindlam).

Lisaks on `recordings/recordings.csv` — originaalsalvestuste nimekiri (nimi, kestus, vaikimisi silt).

## Kaustad

```
recordings/
├── original/            # täispikad salvestused: YYYYMMDD_NNN_<nimi>.wav
├── clips/<label>/       # 1.0 s klipid sildi kaupa: <clip_id>.wav
├── labels.csv           # klippide sildid
└── recordings.csv       # originaalsalvestuste metaandmed
```

Kõik failid: WAV, 16-bit PCM, mono, 16 000 Hz. Kaust `recordings/` luuakse automaatselt ja on `.gitignore`-is.

## Edaspidi

- **ESP32 integratsioon** — CSV-s on juba väli `source` (`esp32`). `POST /api/upload-recording` võtab vastu ka
  multipart-päringu (`audio` fail + väljad `name`, `default_label`, `source=esp32`).
- **Edge Impulse export** — klipid on juba kaustades sildi kaupa (`clips/<label>/`), mis sobib otse
  Edge Impulse'i üleslaadimiseks (silt kaustanime järgi).

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

## Sildid (12 klassi)

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
| `taust`            | taust      | kõik muu, mis pole käsklus ega kõne: vaikus, ruumimüra, muusika, uksed, köha, koer |
| `kone`             | kõne       | tavaline rääkimine, mitte-käsklus kõne    |

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

## Heliallikad

| `source` (CSV) | Kust heli tuleb | Millal saadaval |
|---|---|---|
| `browser` | brauseri mikrofon (Web Audio API) | brauser avab lehe HTTPS-i või `localhost`-i kaudu |
| `pi_i2s` | Raspberry Pi INMP441 I2S mikrofon, salvestab server | rakendus töötab Pi'l ja mikrofon on leitud |
| `esp32` | tulevikus ESP32 üleslaadimised | veel mitte |

Kui mõlemad on saadaval, saab salvestuslehel valida ("Allikas"); valik jäetakse brauserisse meelde.
Brauserid lubavad mikrofoni ainult HTTPS-i või `localhost`-i kaudu — kui avad lehe `http://<pi-ip>:5000`
kaudu (telefon, sülearvuti), peidetakse brauseri mikrofon ja Pi I2S on ainus allikas.
Playback-lehel saab klippe allika järgi filtreerida.

## Käivitamine Raspberry Pi'l

Vajadused:
- Raspberry Pi 3/4/5 Raspberry Pi OS Bookworm või uuem
- INMP441 I2S mikrofon ühendatud (L/R → GND, siis kasutatakse vasakut kanalit)
- I2S overlay seadistatud `/boot/firmware/config.txt`-s (nt `dtoverlay=googlevoicehat-soundcard`)

Install:
```bash
sudo apt install -y libportaudio2 python3-sounddevice python3-scipy python3-numpy
cd ~/voicestrip/Data
python3 -m venv venv --system-site-packages
source venv/bin/activate
pip install -r requirements.txt
python app.py --host 0.0.0.0
```

(Teine võimalus: `pip install sounddevice scipy numpy` venv-is, mis on loodud `--system-site-packages`-iga.)

Ava telefonis või arvutis brauserist:
```
http://<pi-ip>:5000
```

`--host 0.0.0.0` teeb rakenduse kohtvõrgus nähtavaks (vaikimisi ainult `localhost`). Rakendusel pole
parooli ja seal saab salvestusi kustutada — kasuta seda ainult usaldusväärses võrgus.

Käivitamisel kirjutab terminal, kas Pi mikrofon leiti (`Pi I2S mikrofon: olemas`). Mikrofoni kontrollimiseks
ava `/pi-mic-test` (link on salvestuslehel Pi allika kõrval): elav tasemeriba ja 3-sekundiline testsalvestus.

Kui mikrofoni ei leita, kuigi see on ühendatud:
- vaata seadmete nimekirja `/pi-mic-test` lehel või `arecord -l`;
- vali seade käsitsi: `PI_MIC_DEVICE=<number või nime osa> python app.py --host 0.0.0.0`;
- `PI_MIC=1` lülitab tuvastuse sisse ka mujal kui Raspberry Pi'l (nt teine Linuxi plaat), `PI_MIC=0` lülitab välja.

Salvestamine: 48 kHz stereo int32 (INMP441 omaformaat) → vasak kanal → 16 kHz mono 16-bit WAV, sama
formaat mis brauseri salvestustel. Edasine töö (kastid, Accept, klipid, CSV) on täpselt sama.

## Edaspidi

- **ESP32 integratsioon** — CSV-s on juba väli `source` (`esp32`). `POST /api/upload-recording` võtab vastu ka
  multipart-päringu (`audio` fail + väljad `name`, `default_label`, `source=esp32`).
- **Edge Impulse export** — klipid on juba kaustades sildi kaupa (`clips/<label>/`), mis sobib otse
  Edge Impulse'i üleslaadimiseks (silt kaustanime järgi).

# Voice Collector

## Mis see on

Hääleandmete kogumise tööriist RGB LED kontrolleri märksõnatuvastuse (keyword spotting) mudeli treenimiseks.
Salvestad pikema heliklipi (brauseri mikrofon, Raspberry Pi I2S mikrofon või ESP32 WiFi mikrofon), paigutad
lainekujule 1-sekundilised kastid märksõnade kohale ja tööriist lõikab neist 16 kHz / 16-bit / mono WAV klipid
koos CSV sildifailiga.

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
| `source`         | `browser`, `pi_i2s` või `esp32`                         |
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
| `esp32` | ESP32-S3 + INMP441, saadab heli WiFi kaudu serverisse | alati valikus; RECORD töötab, kui ESP32 on võrgus (roheline täpp) |

Salvestuslehel saab allika valida ("Allikas"); valik jäetakse brauserisse meelde.
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

## ESP32-S3 + INMP441 (WiFi mikrofon)

ESP32 on "kaugmikrofon": salvestamist juhid brauserist täpselt nagu Pi I2S puhul (RECORD / STOP),
ESP32 saadab heli WiFi kaudu serverisse ja pärast Stop-i avaneb sama lainekuju-redaktor (kastid, Accept,
klipid, CSV). Kood: `esp32/collector/collector.ino` (ESP32) ja `Data/esp32_link.py` + `/api/esp32/*` (server).

### Ühendused

| INMP441 | ESP32-S3 |
|---|---|
| SCK | GPIO 4 |
| WS | GPIO 3 |
| SD | GPIO 2 |
| VDD | 3.3V |
| GND | GND |
| L/R | GND (vasak kanal) |

Olekutuled (MOSFET-plaat): R = GPIO 9, G = GPIO 10, B = GPIO 11. Plaat on **tagurpidi loogikaga**
(LOW = põleb, nagu Pi `inference.py`-s), seega vaikimisi `LED_ACTIVE_LOW 1`. Vale seade paneb kogu riba
täies valges heleduses põlema — 12 V adapter läheb ülekoormusse ja vilgub (lülitub välja-sisse). Kui sinu
plaadil on HIGH = põleb, pane `config.h`-sse `#define LED_ACTIVE_LOW 0`.

### 1. Arduino IDE

1. **Tools → Board → Boards Manager**: paigalda "esp32" (Espressif Systems), versioon **3.x**.
2. Ava `esp32/collector/collector.ino`.
3. Kopeeri `config.example.h` → `config.h` (samas kaustas) ja täida:
   - `WIFI_SSID`, `WIFI_PASSWORD` — ainult **2.4 GHz** võrk;
   - `SERVER_HOST` — selle arvuti/Pi IP, kus jookseb `app.py` (app.py prindib käivitamisel õige rea);
   - `SERVER_PORT` — vaikimisi `5000`.

   `config.h` on `.gitignore`-is, parool git'i ei satu.
4. **Tools**: Board = *ESP32S3 Dev Module*, USB CDC On Boot = *Enabled* (Serial Monitor üle USB),
   PSRAM = *OPI PSRAM* kui plaat on N8R8/N16R8 (muidu *Disabled*). Flash Size vastavalt plaadile.
5. **Upload**, seejärel **Serial Monitor** (115200 baud): näed WiFi IP-d, puhvri suurust ja serveri olekut.

Ilma PSRAM-ita on helipuhver ~4 s, PSRAM-iga ~33 s — nii palju WiFi katkestust elab salvestus üle ilma heli kaotamata.

### 2. Server

ESP32 peab serverini jõudma, seega käivita kohtvõrgus nähtavana:

```powershell
python app.py --host 0.0.0.0
```

Windowsis küsib tulemüür esimesel korral luba — luba Python **Private** võrgus. Kui aken jäi vahele:
*Windows Security → Firewall → Allow an app* → Python → Private. Brauseris ava ikka `http://localhost:5000`
(siis on ka brauseri mikrofon saadaval).

### 3. Kontroll ja salvestamine

1. ESP32 LED muutub roheliseks ja salvestuslehel on "ESP32 mikrofon" kõrval roheline täpp.
2. Ava `/esp32-mic-test` (link "Mikrofoni test →" ESP32 allika kõrval): elav tasemeriba, WiFi signaal ja
   3-sekundiline testsalvestus. Räägi — peak peaks tõusma üle 0.01.
3. Salvestuslehel vali **Allikas: ESP32 mikrofon** → RECORD → räägi → STOP → paiguta kastid → Accept.

Kui leht salvestamise ajal uuesti laadida, jätkab see käimasolevat salvestust (nagu Pi puhul).

### LED-ide tähendus

| LED | olek |
|---|---|
| sinine vilgub | ühendab WiFi-ga |
| kollane vilgub | WiFi olemas, server ei vasta |
| roheline | võrgus, ootab |
| tsüaan | mikrofoni testleht on lahti |
| punane | salvestab |
| punane vilgub kiiresti | üleslaadimine ebaõnnestub (heli kogub puhvrisse) |

Heledus: `#define LED_BRIGHTNESS 24` (0–255) `config.h`-s; `0` hoiab LED-id kustu.

### Tõrkeotsing

- **Täpp jääb halliks / LED vilgub kollaselt** — vale `SERVER_HOST`, `app.py` pole käivitatud `--host 0.0.0.0`-ga,
  tulemüür blokeerib või ESP32 ja arvuti on eri võrkudes (külaliste WiFi). Kontrolli telefonist `http://<ip>:5000`.
- **Tase alati täpselt 0** — kontrolli SD/WS/SCK juhtmeid ja et L/R on GND-s. Kui L/R on VDD-s, lisa `config.h`-sse
  `#define I2S_MIC_SLOT I2S_STD_SLOT_RIGHT`.
- **Hoiatus "ESP32 puhver sai täis"** — WiFi oli liiga aeglane ja osa heli jäi vahele. Vii ESP32 ruuterile lähemale
  (signaal on näha testlehel, alla −75 dBm on nõrk).
- **Liiga vaikne** — `GAIN_SHIFT 16` annab sama taseme mis Pi I2S salvestused (sama mikrofon, sama skaala —
  hea treeningandmete ühtluse jaoks). `14` teeb 4× valjemaks, aga siis erineb tase Pi andmetest.

### Kuidas see töötab

ESP32 on alati HTTP klient (serveri jaoks pole vaja ESP32 aadressi teada):

- ootel: `GET /api/esp32/poll` iga 300 ms (ühtlasi "elusolek" — 3 s vaikust = võrgust väljas);
- salvestamisel: `POST /api/esp32/chunk?stream=<id>&seq=<n>&dropped=<k>&final=<0|1>`, keha = ~0.25 s toorest
  16 kHz int16 PCM-i;
- iga vastus on üks tekstirida: `idle`, `stream <id>` või `stop <id>`.

STOP-i peale saadab ESP32 puhvrisse jäänud heli (viimane tükk `final=1`), server ootab seda kuni paar sekundit ja
salvestab WAV-i tavalise `store_recording()`-iga (`source=esp32`). Korduvaid tükke (kadunud vastuse järel)
tuntakse `seq` järgi ära. Kui ESP32-st pole 6 s heli tulnud, peatab brauser salvestuse ise ja säilitab saadud osa.

## Salvestuste toomine Pi'lt arvutisse

Pi'l ja arvutis töötab sama koguja, kumbki oma `recordings/` kaustaga. Mõlemad annavad
id-d `next_recording_id()`-ga — "YYYYMMDD_ + 1 + tänane suurim" — aga loevad ainult
**oma** ketast. Seega tekib samal päeval mõlemal `20261025_001`, kahe täiesti erineva
salvestuse kohta. Kaustade kokkukopeerimine kirjutaks ühe teisega üle ja jätaks
`labels.csv`-sse kaks tähendust ühe `clip_id` jaoks.

**1. Kopeeri Pi `recordings/` kaust eraldi kausta** (mitte otse `Data/recordings` peale):

```bash
# WSL-ist, katkestuse järel jätkab samast kohast
wsl rsync -avh --partial --progress pi@raspberrypi.local:~/voicestrip/Data/recordings/ ~/incoming/recordings/
```

Ilma WSL-ita töötab ka `scp -r pi@raspberrypi.local:~/voicestrip/Data/recordings ./incoming`,
aga see alustab iga kord algusest.

**2. Impordi.** Peata koguja (`app.py`) mõlemas masinas. Kõigepealt kuivalt:

```powershell
venv\Scripts\python.exe scripts\import_drop.py --from C:\incoming\recordings
venv\Scripts\python.exe scripts\import_drop.py --from C:\incoming\recordings --apply
```

Skript tunneb salvestuse ära **heli järgi**, mitte id järgi:

| olukord | mis juhtub |
|---|---|
| id on siin vaba | jääb oma id-le |
| id on võetud, heli sama | juba imporditud, jäetakse vahele |
| id on võetud, heli erinev | saab järgmise vaba id ja `recording_id`, `clip_id`, `clip_path` ning `original_path` kirjutatakse ümber |

Uus id väldib nii siinseid id-sid kui ka neid, mida sama import endale jätab. Juba
imporditud salvestust tuntakse ära ka siis, kui ta sai siin uue id — seega **teistkordne
käivitamine ei tee midagi** ja katkenud kopeerimist võib julgelt korrata.

Teatatakse, aga ei peata: `labels.csv` rida, mille WAV-i ei tulnud kaasa (jäetakse välja,
`--allow-missing-audio` võtab need ka), ja WAV, mida `labels.csv` ei maini. Olemasolevaid
faile üle ei kirjutata ja mõlemast CSV-st tehakse enne muutmist `.bak` koopia.

## Edaspidi

- **Edge Impulse export** — klipid on juba kaustades sildi kaupa (`clips/<label>/`), mis sobib otse
  Edge Impulse'i üleslaadimiseks (silt kaustanime järgi).

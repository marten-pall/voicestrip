# Voice trainer

## Mis see on

Häälkäskluste klassifikaator (keyword spotting) RGB LED kontrollerile. Treenitakse kollektori
(`../Data/`) kogutud 1-sekundilistel WAV-klippidel: 16 kHz, mono, kuni 12 klassi (`data.classes` valib alamhulga).

Torustik: `labels.csv` → stratifitseeritud train/val/test jaotus → augmentatsioon (nihe, helitugevus,
taustamüra) → log-mel või MFCC tunnused (GPU-l) → SpecAugment → sinu CNN (`models/my_model.py`) →
kontrollpunktid + segadusmaatriks.

Sihtkeskkond: Ubuntu + GTX 1650 (4 GB), SSH + tmux. Töötab ka CPU-l (aeglasemalt).

## Setup Ubuntu/SSH

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Windowsis aktiveeri venv käsuga `venv\Scripts\activate`.

Kontrolli, et GPU on nähtav: `python -c "import torch; print(torch.cuda.is_available())"`.
Kui tulemus on `False`, paigalda oma CUDA versioonile sobiv torch (vt https://pytorch.org/get-started/locally/).

Andmed loetakse kohast `../Data/recordings/` (vt `config.yaml` → `data.clips_dir`, `data.csv_path`).
Suhtelised teed arvutatakse `trainer/` kausta suhtes, seega skripte võib käivitada ükskõik kust.

## Käivitamine tmux-is

```bash
tmux new -s train
python train.py --config config.yaml
# Ctrl+B D (detach)
tmux attach -t train
```

Üksikuid väärtusi saab üle kirjutada ilma configi muutmata (nt kiire suitsutest):

```bash
python train.py --config config.yaml -o training.epochs=2
python train.py --config config.yaml -o features.type=mfcc
```

Iga treening loob kausta `runs/<run_name>_<YYYYMMDD_HHMMSS>/`:

| fail                   | sisu                                                          |
|------------------------|---------------------------------------------------------------|
| `config.yaml`          | kasutatud config (koos `-o` muudatustega)                     |
| `splits.json`          | train/val/test `clip_id`-d (eval kasutab sama testijaotust)   |
| `metrics.csv`          | epohhi kaupa train/val loss + täpsus + LR                     |
| `best.pt` / `last.pt`  | parim (val täpsuse järgi) ja viimane kontrollpunkt            |
| `confusion_matrix.png` | testihulga segadusmaatriks                                    |
| `training_log.txt`     | kogu logi (sh batch-taseme read)                              |

### Hindamine

```bash
python eval.py --checkpoint runs/<run>/best.pt                  # salvestatud testijaotus
python eval.py --checkpoint runs/<run>/best.pt --split all      # kõik klipid
python eval.py --checkpoint runs/<run>/best.pt --wav-dir minu_wavid/
```

`--wav-dir`: kui WAV on sildinimelises kaustas (`minu_wavid/punane/x.wav`), arvutatakse täpsus;
muidu prinditakse ainult ennustused.

## Oma mudeli ehitamine

Mudel elab failis `models/my_model.py`. Täida `__init__` ja `forward`:

```python
@register_model("my_model")
class MyModel(nn.Module):
    def __init__(self, num_classes, channels=32, dropout=0.3):
        super().__init__()
        # sinu kihid siin
        ...

    def forward(self, x):
        # x: [B, 1, n_mels, n_frames]
        # tagasta: [B, num_classes]
        ...
```

Lisa oma hüperparameetrid `config.yaml` → `model.args` alla:

```yaml
model:
  name: my_model
  args:
    channels: 32
    dropout: 0.3
```

`num_classes` lisatakse automaatselt — see on klasside arv `data.classes`-is (praegu 5). Kui paned selle
ise `model.args` alla ja see ei klapi, annab treening vea.

Train:
```bash
python train.py --config config.yaml
```

Enne kui mudel on valmis, lõpeb treening veaga `NotImplementedError: Build your model in
trainer/models/my_model.py ...` — see tähendab, et kõik muu on paigas.

Teiste mudelite lisamiseks: loo uus fail `models/<nimi>.py`, dekoreeri klass
`@register_model("<nimi>")`, muuda `config.yaml` → `model.name` sellele nimele.
Kõik `models/` kausta failid imporditakse automaatselt.

## Ainult osa klassidega treenimine

Kui kõigi 12 klassi jaoks pole veel andmeid, vali `data.classes` abil alamhulk — mudeli väljundkiht
tehakse täpselt nii suur, teiste siltide klipid jäetakse välja:

```yaml
data:
  classes: [kone, punane, sisse, taust, valja]   # null = kõik 12
```

või ilma configi muutmata:

```bash
python train.py --config config.yaml -o "data.classes=[kone, punane, sisse, taust, valja]"
```

- Klassid sorteeritakse tähestikuliselt; väljundi indeks `i` = `classes[i]`. Nimekiri salvestatakse
  kontrollpunkti (`classes`), `eval.py` ja hilisem eksport loevad selle sealt.
- `taust` klippe kasutatakse müra segamiseks ka siis, kui `taust` pole ise klass.
- Kui lisad hiljem klassi, treeni uuesti — erineva klasside arvuga kontrollpunkte ei saa omavahel vahetada.

## Train/val/test jaotus ja lekkekontroll

Kollektoris võivad kastid kattuda. Kui kaks kattuvat klippi satuvad üks treeningusse ja teine testi, on
testitulemus liiga hea (mudel on testiklippi sisuliselt juba näinud). `data.split_by` määrab, mis peab
jaotamisel koos püsima:

| `split_by` | mis jääb kokku | millal |
|---|---|---|
| `clip` | mitte midagi (iga klipp eraldi) | ainult võrdluseks vanade jooksudega — kattuvad kastid lekivad |
| `overlap` (vaikimisi) | sama salvestise ajaliselt kattuvad klipid | tavaline valik |
| `recording` | terved salvestised | aus „uue sessiooni“ test; vajab igale klassile ≥ 3 salvestist |

Iga treeningu logi alguses on lekkekontroll, näiteks:

```
Leak check test: 0/76 clips overlap a train clip's audio, 76/76 come from a recording that is also in train
```

Esimene arv peab `overlap`/`recording` puhul olema 0. Teine näitab, kui palju testiklippe on samast
sessioonist (ruum, mikrofon, hääl) kui treeningklipid — mida suurem, seda optimistlikum on testitulemus.

## Config overview

Kõik võtmed on kohustuslikud — puuduv või valesti kirjutatud võti annab kohe vea (vaikeväärtusi koodis pole).

- **data** — CSV ja klippide asukoht, `classes` (alamhulk või `null`), `split_by` (vt ülal), diskreetimissagedus, klipi pikkus (lühem → nullidega, pikem → keskelt lõigatud),
  val/test osakaal, seeme, DataLoaderi töötajad. Klassid, kus on alla 3 klipi, lähevad ainult treeningusse.
- **features** — `melspec` või `mfcc`, FFT/akna/hüppe parameetrid, normaliseerimine
  (keskmine/std arvutatakse treeninghulgalt ja salvestatakse kontrollpunkti).
- **augment** — ainult treeningul: ajanihe, helitugevus, `taust` klippide segamine juhusliku SNR-iga,
  SpecAugment (sagedus- ja ajamaskid). `enabled: false` lülitab kõik välja.
- **model** — registri nimi + konstruktori argumendid. `num_classes` tuletatakse `data.classes`-ist, configis seda pole.
- **training** — batch, epohhid, optimeerija (`adamw`/`adam`/`sgd`), LR-graafik (`cosine`/`step`/`none` + warmup),
  gradiendi lõikamine, early stopping, klasside kaalumine, mixed precision (ainult CUDA-l).
- **logging** — jooksu nimi, kaust, batch-logi sagedus, TensorBoard (`pip install tensorboard`),
  `save_best_only: false` salvestab iga epohhi.

## Teadmiseks

- Sildid on failis `labels.py` (tähestikulises järjekorras, indeksid sõltuvad sellest) — hoia kooskõlas
  kollektori `LABELS`-iga.
- Kollektoris võivad kastid kattuda. Kui sama salvestuse kattuvad klipid satuvad eri jaotustesse,
  võib testitäpsus olla optimistlik.
- WAV-id loetakse standardse `wave` mooduliga (torchaudio ≥ 2.9 `load()` vajaks TorchCodecit + FFmpegi).

## Edaspidi

- ESP32 export: PyTorch → ONNX → TensorFlow Lite (int8 kvantimine) → TFLite Micro. Hoia mudel väike
  (kümned tuhanded parameetrid), et see mikrokontrollerisse mahuks.
- Tunnuste arvutus seadmes peab vastama `features` sektsioonile (sama mel-filterpank, hop, normaliseerimine).

# Voice trainer

## Mis see on

Häälkäskluste klassifikaator (keyword spotting) RGB LED kontrollerile. Treenitakse kollektori
(`../Data/`) kogutud 1-sekundilistel WAV-klippidel: 16 kHz, mono, kuni 13 klassi (`data.classes` valib alamhulga).

Torustik: `labels.csv` → stratifitseeritud train/val/test jaotus → augmentatsioon (nihe, helitugevus,
taustamüra) → log-mel või MFCC tunnused (GPU-l) → SpecAugment → CNN → kontrollpunktid + segadusmaatriks.

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
python train.py --config config.yaml -o model.name=ds_cnn -o features.type=mfcc
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

## Oma mudeli lisamine

Loo fail `models/my_model.py` — see registreeritakse automaatselt, muid faile muutma ei pea:

```python
from .registry import register_model
import torch.nn as nn

@register_model("my_model")
class MyModel(nn.Module):
    def __init__(self, num_classes, hidden_dim=128):
        super().__init__()
        # ...
    def forward(self, x):          # x: [B, 1, n_features, n_frames], nt [B, 1, 40, 101]
        return self.head(x)
```

Siis `config.yaml`:

```yaml
model:
  name: my_model
  args:
    hidden_dim: 256        # num_classes lisatakse automaatselt (= klasside arv)
```

Olemasolevad mudelid: `baseline_cnn` (vaikimisi, ~24k parameetrit) ja `ds_cnn`
(Depthwise-Separable CNN, Zhang et al. 2017, ~23k parameetrit — sobib hiljem mikrokontrollerile).

## Transfer learning (valikuline)

Kiirem konvergents, vähem andmeid vaja. Kasutab DS-CNN mudelit (ARM ML-KWS arhitektuur),
eeltreenitud 35 ingliskeelse sõna peal (Google Speech Commands v2). Vaikimisi (`config.yaml`)
treenitakse nullist — transfer learning lülitub sisse ainult `configs/transfer_*.yaml` kaudu.

### Variant 1: lae alla valmis kaalud
Vaata `pretrained/README.md`. NB: ARM-i ametlikud kaalud on TensorFlow graafid teistsuguste
tunnuste jaoks (10 MFCC × 49 kaadrit) ega sobi siia otse — seepärast on soovitatav variant 2.

### Variant 2: treeni ise Speech Commands peal (üks kord, ~2 tundi GTX 1650-l)
```bash
python scripts/pretrain_on_speech_commands.py --epochs 30
```
Laeb alla ~2.3 GB andmestiku (`~/.cache/torchaudio`) ja salvestab `pretrained/ds_cnn_speech_commands.pt`.
Kasutab täpselt samu tunnuseid kui `configs/transfer_ds_cnn.yaml` — `train.py` kontrollib seda
ja keeldub alustamast, kui tunnuste seaded erinevad.

### Treeni oma andmetel transfer learning'uga
```bash
python train.py --config configs/transfer_ds_cnn.yaml           # fine-tune kõik kihid
python train.py --config configs/transfer_ds_cnn_frozen.yaml    # ainult classifier head (kiireim)
python train.py --config configs/scratch.yaml                   # võrdluseks: nullist
```
Logi alguses on näha `Total params / Trainable params (frozen: ...)` — külmutatud variandis
treenitakse ainult viimast kihti (13 klassiga 845 kaalu, 6 klassiga 390). Külmutatud kihtide BatchNorm statistika jääb samuti muutmata.

Variandid kirjutavad eri kaustadesse (`runs/scratch_baseline_v1_*`, `runs/transfer_ds_cnn_v1_*`,
`runs/transfer_ds_cnn_frozen_v1_*`); võrdle `metrics.csv` ja logi lõpus olevat testitulemust.

`configs/*.yaml` algavad reaga `base: ../config.yaml` ja sisaldavad ainult erinevusi
(sektsioonid liidetakse; `model.args` asendatakse tervikuna).

### Soovitus
- **Alusta `config.yaml` (scratch)** — kui >90% accuracy saavutad, tee valmis
- **Kui <90% või tahad vähemate andmetega** → `configs/transfer_ds_cnn_frozen.yaml`
- **Kui ikka vähe** → `configs/transfer_ds_cnn.yaml` (full fine-tune)

## Ainult osa klassidega treenimine

Kui kõigi 13 klassi jaoks pole veel andmeid, vali `data.classes` abil alamhulk — mudeli väljundkiht
tehakse täpselt nii suur, teiste siltide klipid jäetakse välja:

```yaml
data:
  classes: [kone, punane, sisse, taust, tundmatu, valja]   # null = kõik 13
```

või ilma configi muutmata:

```bash
python train.py --config configs/transfer_ds_cnn_frozen.yaml -o "data.classes=[kone, punane, sisse, taust, tundmatu, valja]"
```

- Klassid sorteeritakse tähestikuliselt; väljundi indeks `i` = `classes[i]`. Nimekiri salvestatakse
  kontrollpunkti (`classes`), `eval.py` ja hilisem eksport loevad selle sealt.
- `taust` klippe kasutatakse müra segamiseks ka siis, kui `taust` pole ise klass.
- Kui lisad hiljem klassi, treeni uuesti — erineva klasside arvuga kontrollpunkte ei saa omavahel vahetada.

## Config overview

Kõik võtmed on kohustuslikud — puuduv või valesti kirjutatud võti annab kohe vea (vaikeväärtusi koodis pole).

- **data** — CSV ja klippide asukoht, `classes` (alamhulk või `null`), diskreetimissagedus, klipi pikkus (lühem → nullidega, pikem → keskelt lõigatud),
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

- ESP32 export: PyTorch → ONNX → TensorFlow Lite (int8 kvantimine) → TFLite Micro.
  `ds_cnn` on selleks parem lähtepunkt kui `baseline_cnn`.
- Tunnuste arvutus seadmes peab vastama `features` sektsioonile (sama mel-filterpank, hop, normaliseerimine).

"""DS-CNN from "Hello Edge: Keyword Spotting on Microcontrollers" (Zhang et al., 2017),
the ARM ML-KWS-for-MCU reference architecture. Defaults = DS-CNN-S (64 ch, 4 blocks).

Optional transfer learning: `pretrained_path` loads a checkpoint produced by
scripts/pretrain_on_speech_commands.py (any state_dict with matching backbone keys
works); the classifier head is always re-initialised for `num_classes`.
"""

from __future__ import annotations

import logging
from pathlib import Path

import torch
from torch import nn

from .registry import register_model

log = logging.getLogger(__name__)


@register_model("ds_cnn")
class DSCNN(nn.Module):
    """Input: [B, 1, n_mels, n_frames] (default 40 x 101 for 1 s at 16 kHz)."""

    def __init__(
        self,
        num_classes: int,
        n_mels: int = 40,  # documents the expected input; the network itself is size-agnostic
        channels: int = 64,
        n_blocks: int = 4,
        dropout: float = 0.2,
        pretrained_path: str | None = None,
        freeze_backbone: bool = False,
    ) -> None:
        super().__init__()
        self.n_mels = n_mels
        self.backbone_frozen = False
        self.conv1 = nn.Sequential(
            nn.Conv2d(1, channels, kernel_size=(10, 4), stride=(2, 2), padding=(5, 1)),
            nn.BatchNorm2d(channels),
            nn.ReLU(inplace=True),
        )
        self.blocks = nn.ModuleList([self._ds_block(channels, channels) for _ in range(n_blocks)])
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.dropout = nn.Dropout(dropout)
        self.classifier = nn.Linear(channels, num_classes)

        if pretrained_path:
            self._load_pretrained(Path(pretrained_path))
        if freeze_backbone:
            if not pretrained_path:
                log.warning("[DSCNN] freeze_backbone=true without pretrained_path: freezing random weights")
            self._freeze_backbone()

    @staticmethod
    def _ds_block(in_c: int, out_c: int) -> nn.Sequential:
        return nn.Sequential(
            nn.Conv2d(in_c, in_c, kernel_size=3, padding=1, groups=in_c, bias=False),  # depthwise
            nn.BatchNorm2d(in_c),
            nn.ReLU(inplace=True),
            nn.Conv2d(in_c, out_c, kernel_size=1, bias=False),  # pointwise
            nn.BatchNorm2d(out_c),
            nn.ReLU(inplace=True),
        )

    def _load_pretrained(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(
                f"Pretrained checkpoint not found: {path}\n"
                "See pretrained/README.md (or run scripts/pretrain_on_speech_commands.py).")
        # weights_only: refuses pickled code, so a downloaded file cannot execute anything
        state = torch.load(path, map_location="cpu", weights_only=True)
        if "state_dict" in state:
            state = state["state_dict"]
        # The pretrained head has a different number of classes (e.g. 35 Speech Commands words).
        own = self.state_dict()
        state = {k: v for k, v in state.items()
                 if not k.startswith("classifier.") and not (k in own and own[k].shape != v.shape)}
        missing, unexpected = self.load_state_dict(state, strict=False)
        missing_backbone = [k for k in missing if not k.startswith("classifier.")]
        if missing_backbone:
            raise ValueError(f"Pretrained checkpoint does not match this DS-CNN (missing {missing_backbone[:5]}...). "
                             "Check model.args channels / n_blocks.")
        log.info("[DSCNN] Pretrained loaded. Missing: %d (expected classifier.*), unexpected: %d",
                 len(missing), len(unexpected))

    def _freeze_backbone(self) -> None:
        for name, p in self.named_parameters():
            if not name.startswith("classifier."):
                p.requires_grad = False
        self.backbone_frozen = True
        log.info("[DSCNN] Backbone frozen; only classifier is trainable.")

    def train(self, mode: bool = True) -> "DSCNN":
        # A frozen backbone must also keep its BatchNorm running stats frozen.
        super().train(mode)
        if self.backbone_frozen:
            self.conv1.eval()
            self.blocks.eval()
        return self

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.conv1(x)
        for block in self.blocks:
            x = block(x)
        x = self.pool(x).flatten(1)
        x = self.dropout(x)
        return self.classifier(x)

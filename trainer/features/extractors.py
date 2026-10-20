"""Waveform -> 2-D features for the CNNs.

Extractors are nn.Modules so they run batched on the GPU inside the training
loop: [B, 1, N] -> [B, 1, n_features, n_frames] (a single clip [1, N] gives
[1, n_features, n_frames]). Per-feature mean/std are buffers, fitted once on
the training set and saved with the checkpoint.
"""

from __future__ import annotations

import logging

import torch
import torchaudio.transforms as T
from torch import nn
from torch.utils.data import DataLoader

from utils.config import FeaturesConfig

log = logging.getLogger(__name__)


class FeatureExtractor(nn.Module):
    def __init__(self, n_features: int, normalize: bool, norm_eps: float) -> None:
        super().__init__()
        self.n_features = n_features
        self.normalize = normalize
        self.norm_eps = norm_eps
        self.register_buffer("mean", torch.zeros(n_features, 1))
        self.register_buffer("std", torch.ones(n_features, 1))
        self.register_buffer("fitted", torch.tensor(False))

    def compute(self, wav: torch.Tensor) -> torch.Tensor:
        """Unnormalised features; implemented by subclasses."""
        raise NotImplementedError

    def forward(self, wav: torch.Tensor) -> torch.Tensor:
        features = self.compute(wav)
        if self.normalize:
            if not bool(self.fitted):
                raise RuntimeError("normalize=true but fit_stats() was never called / loaded")
            features = (features - self.mean) / self.std
        return features

    @torch.no_grad()
    def fit_stats(self, loader: DataLoader, device: torch.device) -> None:
        """Per-feature mean/std over every frame of every clip in `loader`."""
        total = torch.zeros(self.n_features, dtype=torch.float64, device=device)
        total_sq = torch.zeros_like(total)
        count = 0
        for wav, _ in loader:
            feats = self.compute(wav.to(device)).double()          # [B, 1, F, T]
            total += feats.sum(dim=(0, 1, 3))
            total_sq += feats.pow(2).sum(dim=(0, 1, 3))
            count += feats.shape[0] * feats.shape[1] * feats.shape[3]
        if count == 0:
            raise ValueError("Cannot fit normalization stats on an empty dataset")
        mean = total / count
        std = (total_sq / count - mean.pow(2)).clamp_min(0).sqrt().clamp_min(self.norm_eps)
        self.mean.copy_(mean.float().unsqueeze(1))
        self.std.copy_(std.float().unsqueeze(1))
        self.fitted.fill_(True)
        log.info("Feature stats fitted on %d frames (mean %.3f, std %.3f averaged over features)",
                 count, mean.mean().item(), std.mean().item())


class MelSpectrogramExtractor(FeatureExtractor):
    """log(mel power + eps)."""

    def __init__(self, cfg: FeaturesConfig, sample_rate: int) -> None:
        super().__init__(cfg.n_mels, cfg.normalize, cfg.norm_eps)
        self.log_eps = cfg.log_eps
        self.mel = T.MelSpectrogram(
            sample_rate=sample_rate, n_fft=cfg.n_fft, win_length=cfg.win_length,
            hop_length=cfg.hop_length, n_mels=cfg.n_mels,
        )

    def compute(self, wav: torch.Tensor) -> torch.Tensor:
        return torch.log(self.mel(wav) + self.log_eps)


class MFCCExtractor(FeatureExtractor):
    def __init__(self, cfg: FeaturesConfig, sample_rate: int) -> None:
        super().__init__(cfg.n_mfcc, cfg.normalize, cfg.norm_eps)
        self.mfcc = T.MFCC(
            sample_rate=sample_rate, n_mfcc=cfg.n_mfcc, log_mels=True,
            melkwargs={"n_fft": cfg.n_fft, "win_length": cfg.win_length,
                       "hop_length": cfg.hop_length, "n_mels": cfg.n_mels},
        )

    def compute(self, wav: torch.Tensor) -> torch.Tensor:
        return self.mfcc(wav)


_EXTRACTORS: dict[str, type[FeatureExtractor]] = {
    "melspec": MelSpectrogramExtractor,
    "mfcc": MFCCExtractor,
}


def feature_signature(cfg: FeaturesConfig, sample_rate: int, clip_duration_sec: float) -> dict:
    """Everything that changes what the network sees. Pretrained weights are only
    meaningful when the fine-tuning run produces identical features."""
    sig = {
        "type": cfg.type, "sample_rate": sample_rate, "clip_duration_sec": clip_duration_sec,
        "n_mels": cfg.n_mels, "n_fft": cfg.n_fft, "hop_length": cfg.hop_length, "win_length": cfg.win_length,
    }
    if cfg.type == "melspec":
        sig["log_eps"] = cfg.log_eps
    if cfg.type == "mfcc":
        sig["n_mfcc"] = cfg.n_mfcc
    return sig


def get_extractor(cfg: FeaturesConfig, sample_rate: int) -> FeatureExtractor:
    if cfg.type not in _EXTRACTORS:
        raise KeyError(f"Unknown features.type '{cfg.type}'. Available: {list(_EXTRACTORS)}")
    return _EXTRACTORS[cfg.type](cfg, sample_rate)

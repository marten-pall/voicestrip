"""Augmentations.

Waveform-domain ones ([1, N] -> [1, N]) run inside the training Dataset's
__getitem__ (CPU, DataLoader workers). SpecAugment works on batched features
[B, 1, F, T] and is applied in the training loop after feature extraction.
All randomness uses torch's RNG, which DataLoader seeds per worker.
"""

from __future__ import annotations

import logging
from pathlib import Path

import torch
import torchaudio.transforms as T
from torch import nn

from utils.config import AugmentConfig, SpecAugmentConfig

from .dataset import fix_length, load_wav

log = logging.getLogger(__name__)


def _uniform(low: float, high: float) -> float:
    return low + (high - low) * torch.rand(1).item()


class TimeShift:
    """Shift by up to +-max_shift samples, zero-filling (no wrap-around, so the
    end of a word never reappears at the start)."""

    def __init__(self, max_shift: int) -> None:
        self.max_shift = max_shift

    def __call__(self, wav: torch.Tensor) -> torch.Tensor:
        if self.max_shift <= 0:
            return wav
        shift = int(torch.randint(-self.max_shift, self.max_shift + 1, (1,)))
        if shift == 0:
            return wav
        out = torch.zeros_like(wav)
        if shift > 0:
            out[..., shift:] = wav[..., :-shift]
        else:
            out[..., :shift] = wav[..., -shift:]
        return out


class VolumeJitter:
    def __init__(self, amount: float) -> None:
        self.amount = amount

    def __call__(self, wav: torch.Tensor) -> torch.Tensor:
        return wav * _uniform(1 - self.amount, 1 + self.amount)


class NoiseMix:
    """With probability `prob`, add a random background clip at a random SNR."""

    def __init__(self, noise_paths: list[Path], prob: float, snr_db: tuple[float, float],
                 sample_rate: int, num_samples: int, eps: float = 1e-10) -> None:
        self.noise_paths = list(noise_paths)
        self.prob = prob
        self.snr_db = snr_db
        self.sample_rate = sample_rate
        self.num_samples = num_samples
        self.eps = eps

    def __call__(self, wav: torch.Tensor) -> torch.Tensor:
        if not self.noise_paths or torch.rand(1).item() >= self.prob:
            return wav
        path = self.noise_paths[int(torch.randint(len(self.noise_paths), (1,)))]
        noise = load_wav(path, self.sample_rate)
        if noise.shape[-1] > self.num_samples:  # long noise file: random 1 s window
            start = int(torch.randint(noise.shape[-1] - self.num_samples + 1, (1,)))
            noise = noise[..., start:start + self.num_samples]
        noise = fix_length(noise, self.num_samples)
        signal_power = wav.pow(2).mean()
        noise_power = noise.pow(2).mean()
        if signal_power < self.eps or noise_power < self.eps:
            return wav  # silent signal or silent noise: SNR is undefined
        snr = _uniform(*self.snr_db)
        scale = torch.sqrt(signal_power / (noise_power * 10 ** (snr / 10)))
        return (wav + scale * noise).clamp(-1.0, 1.0)


class Compose:
    def __init__(self, transforms: list) -> None:
        self.transforms = transforms

    def __call__(self, wav: torch.Tensor) -> torch.Tensor:
        for transform in self.transforms:
            wav = transform(wav)
        return wav

    def __repr__(self) -> str:
        return f"Compose({[type(t).__name__ for t in self.transforms]})"


def build_waveform_augment(cfg: AugmentConfig, noise_paths: list[Path],
                           sample_rate: int, num_samples: int) -> Compose | None:
    if not cfg.enabled:
        return None
    transforms: list = [
        TimeShift(int(round(cfg.time_shift_ms / 1000 * sample_rate))),
        VolumeJitter(cfg.volume_jitter),
    ]
    if cfg.noise_mix_prob > 0:
        if noise_paths:
            low, high = cfg.noise_snr_db
            transforms.append(NoiseMix(noise_paths, cfg.noise_mix_prob, (float(low), float(high)),
                                       sample_rate, num_samples))
        else:
            log.warning("augment.noise_mix_prob > 0 but there are no `taust` clips in the train split - noise mix disabled")
    return Compose(transforms)


class SpecAugment(nn.Module):
    """Frequency + time masking on [B, 1, F, T], independent masks per example."""

    def __init__(self, cfg: SpecAugmentConfig) -> None:
        super().__init__()
        masks: list[nn.Module] = []
        masks += [T.FrequencyMasking(cfg.freq_mask_param, iid_masks=True) for _ in range(cfg.n_freq_masks)]
        masks += [T.TimeMasking(cfg.time_mask_param, iid_masks=True) for _ in range(cfg.n_time_masks)]
        self.masks = nn.Sequential(*masks)

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.masks(features)


def build_spec_augment(cfg: AugmentConfig) -> SpecAugment | None:
    if not (cfg.enabled and cfg.specaugment.enabled):
        return None
    return SpecAugment(cfg.specaugment)

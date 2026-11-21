"""
Your custom CNN model for voice command classification.

Fill in __init__ and forward below. Use the training pipeline (train.py)
without modification — it reads this via the registry.

Input tensor shape (standard):
    x: [B, 1, n_mels, n_frames]
    - B = batch size
    - 1 = single-channel feature map
    - n_mels = features.n_mels in utils/config.py (default 40); n_mfcc (13) if features.type is mfcc
    - n_frames = 97 for 1 s audio at 16 kHz (n_fft=512, hop_length=160, center=False)

Output tensor shape:
    logits: [B, num_classes]
    - num_classes = number of classes in data.classes (utils/config.py); train.py
      passes it in automatically, you don't put it in model.args
    - Return raw logits (no softmax). Training loop applies CrossEntropyLoss,
      which includes softmax internally.
"""

import torch
import torch.nn as nn

from .registry import register_model


@register_model("my_model")
class MyModel(nn.Module):
    def __init__(self, num_classes: int, channels: int = 64, kernel_size: int = 3, pool_size: int = 2,
                 dropout: float = 0.2) -> None:
        """Hyperparameters come from utils/config.py -> model.args (num_classes from data.classes)."""
        super().__init__()
        padding = kernel_size // 2
        self.conv1 = nn.Conv2d(in_channels=1, out_channels=channels, kernel_size=kernel_size, padding=padding)
        self.BatchNorm1 = nn.BatchNorm2d(num_features=channels)
        self.conv2 = nn.Conv2d(in_channels=channels, out_channels=channels, kernel_size=kernel_size, padding=padding)
        self.BatchNorm2 = nn.BatchNorm2d(num_features=channels)
        self.relu = nn.ReLU()  # plain ReLU: cheapest activation for int8 TFLite Micro on the ESP32
        self.pool = nn.MaxPool2d(pool_size)
        self.dropout = nn.Dropout(dropout)
        self.Output = nn.Linear(channels, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [B, 1, n_mels, n_frames] mel-spectrogram or MFCC features

        Returns:
            logits: [B, num_classes]
        """
        x = self.conv1(x)            # [B, 1, 40, 97] -> [B, C, 40, 97]
        x = self.BatchNorm1(x)
        x = self.relu(x)
        x = self.pool(x)             # -> [B, C, 20, 48]

        x = self.conv2(x)
        x = self.BatchNorm2(x)
        x = self.relu(x)

        # Collapse the time/frequency grid so the classifier sees one value per
        # channel. nn.Linear works on the LAST dimension, so without this it
        # would try to multiply the 48 frames by a weight expecting C.
        x = x.mean(dim=(2, 3))       # -> [B, C]
        x = self.dropout(x)
        x = self.Output(x)           # -> [B, num_classes]
        return x
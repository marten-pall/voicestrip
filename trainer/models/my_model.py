"""
Your custom CNN model for voice command classification.

Fill in __init__ and forward below. Use the training pipeline (train.py)
without modification — it reads this via the registry.

Input tensor shape (standard):
    x: [B, 1, n_mels, n_frames]
    - B = batch size
    - 1 = single-channel feature map
    - n_mels = features.n_mels in utils/config.py (default 40); n_mfcc (13) if features.type is mfcc
    - n_frames = ~101 for 1s audio at 16kHz with hop_length=160

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
from .configs.cnn_config import CnnConfig as cfg

@register_model("my_model")
class MyModel(nn.Module):
    def __init__(self, in_channels_1 = cfg.in_channels_1, out_channels_1 = cfg.out_channels_1, kernel_size= cfg.kernel_size, dropout = cfg.dropout, pool_size = cfg.pool_size, num_classes = cfg.num_classes):
        """
        Example signature:
            def __init__(self, num_classes, channels=32, dropout=0.3):

        .\venv\Scripts\python.exe train.py
        """
        super().__init__()
        padding = kernel_size // 2
        self.conv1 = nn.Conv2d(in_channels=in_channels_1, out_channels=out_channels_1, kernel_size=kernel_size, padding=padding)
        self.BatchNorm1 = nn.BatchNorm2d(num_features=out_channels_1)
        self.conv2 = nn.Conv2d(in_channels=out_channels_1, out_channels=out_channels_1, kernel_size=kernel_size, padding=padding)
        self.BatchNorm2 = nn.BatchNorm2d(num_features=out_channels_1)
        self.Silu = nn.SiLU()
        self.pool = nn.MaxPool2d(pool_size)
        self.dropout = nn.Dropout(dropout)
        self.Output = nn.Linear(out_channels_1, num_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [B, 1, n_mels, n_frames] mel-spectrogram or MFCC features

        Returns:
            logits: [B, num_classes]
        """
        x = self.conv1(x)            # [B, 1, 40, 101] -> [B, C, 40, 101]
        x = self.BatchNorm1(x)
        x = self.Silu(x)
        x = self.pool(x)             # -> [B, C, 20, 50]

        x = self.conv2(x)
        x = self.BatchNorm2(x)
        x = self.Silu(x)

        # Collapse the time/frequency grid so the classifier sees one value per
        # channel. nn.Linear works on the LAST dimension, so without this it
        # would try to multiply the 50 frames by a weight expecting C.
        x = x.mean(dim=(2, 3))       # -> [B, C]
        x = self.dropout(x)
        x = self.Output(x)           # -> [B, num_classes]
        return x
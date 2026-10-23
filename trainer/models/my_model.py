"""
Your custom CNN model for voice command classification.

Fill in __init__ and forward below. Use the training pipeline (train.py)
without modification — it reads this via the registry.

Input tensor shape (standard):
    x: [B, 1, n_mels, n_frames]
    - B = batch size
    - 1 = single-channel feature map
    - n_mels = features.n_mels from config.yaml (default 40); n_mfcc (13) if features.type is mfcc
    - n_frames = ~101 for 1s audio at 16kHz with hop_length=160

Output tensor shape:
    logits: [B, num_classes]
    - num_classes = number of classes in data.classes (config.yaml); train.py
      passes it in automatically, you don't put it in model.args
    - Return raw logits (no softmax). Training loop applies CrossEntropyLoss,
      which includes softmax internally.
"""

import torch
import torch.nn as nn

from .registry import register_model

TODO_MESSAGE = "Build your model in trainer/models/my_model.py (MyModel.__init__ and MyModel.forward)"


@register_model("my_model")
class MyModel(nn.Module):
    def __init__(self, num_classes: int, **kwargs):
        """
        Build your layers here.

        kwargs come from `model.args` in config.yaml. Add any hyperparameters
        you want to control from config (e.g., channels, dropout, kernel_size)
        as explicit keyword arguments.

        Example signature:
            def __init__(self, num_classes, channels=32, dropout=0.3):
        """
        super().__init__()
        # TODO: define your layers
        raise NotImplementedError(TODO_MESSAGE)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Args:
            x: [B, 1, n_mels, n_frames] mel-spectrogram or MFCC features

        Returns:
            logits: [B, num_classes]
        """
        # TODO: implement forward pass
        raise NotImplementedError(TODO_MESSAGE)

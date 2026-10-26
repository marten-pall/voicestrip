"""CNN architecture hyperparameters"""

from dataclasses import dataclass


@dataclass
class CnnConfig:
    """Backbone CNN hyperparameters"""
    # conv1 reads the feature map itself, which is single-channel:
    # x is [B, 1, n_mels, n_frames], so in_channels must be 1
    in_channels_1: int = 1
    out_channels_1: int = 64
    num_classes: int = 12

    kernel_size: int = 3                               # conv kernel (all blocks)
    pool_size: int = 2                                 # MaxPool2d kernel & stride

    # Progressive dropout per block
    dropout: float = 0.0

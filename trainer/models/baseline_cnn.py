import torch
from torch import nn

from .registry import register_model


def _conv_block(in_ch: int, out_ch: int) -> nn.Sequential:
    return nn.Sequential(
        nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1, bias=False),
        nn.BatchNorm2d(out_ch),
        nn.ReLU(inplace=True),
        nn.MaxPool2d(2),
    )


@register_model("baseline_cnn")
class SimpleCNN(nn.Module):
    """3 x (conv3x3 -> BN -> ReLU -> maxpool) -> global avg pool -> dropout -> linear.

    Works for any input [B, 1, F, T] (mel 40x101, mfcc 13x101, ...) thanks to the
    adaptive pooling; inputs need F, T >= 8 to survive the three 2x poolings.
    """

    def __init__(self, num_classes: int, dropout: float = 0.3, channels: tuple[int, ...] = (16, 32, 64)) -> None:
        super().__init__()
        blocks, in_ch = [], 1
        for out_ch in channels:
            blocks.append(_conv_block(in_ch, out_ch))
            in_ch = out_ch
        self.features = nn.Sequential(*blocks)
        self.pool = nn.AdaptiveAvgPool2d(1)
        self.head = nn.Sequential(nn.Flatten(), nn.Dropout(dropout), nn.Linear(in_ch, num_classes))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.head(self.pool(self.features(x)))

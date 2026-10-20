"""Run logging: stdout (rich if installed) + training_log.txt + metrics.csv + optional TensorBoard.

Library modules log through the standard `logging` module
(`logging.getLogger(__name__)`); `setup_logging` attaches the handlers.
"""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path
from typing import Any

METRIC_FIELDS = ["epoch", "train_loss", "train_acc", "val_loss", "val_acc", "lr"]
_FMT = "%(asctime)s %(levelname)-7s %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(log_file: Path | None = None) -> logging.Logger:
    """Console at INFO, log file at DEBUG (batch-level lines go to the file only)."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    try:
        from rich.logging import RichHandler
        console: logging.Handler = RichHandler(show_path=False, log_time_format=_DATEFMT)
        console.setFormatter(logging.Formatter("%(message)s"))
    except ImportError:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    console.setLevel(logging.INFO)
    root.addHandler(console)

    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(_FMT, _DATEFMT))
        file_handler.setLevel(logging.DEBUG)
        root.addHandler(file_handler)

    for noisy in ("matplotlib", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return logging.getLogger("trainer")


class MetricsLogger:
    """Per-epoch metrics -> metrics.csv (+ TensorBoard scalars if enabled)."""

    def __init__(self, run_dir: Path, tensorboard: bool) -> None:
        self.csv_path = run_dir / "metrics.csv"
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=METRIC_FIELDS).writeheader()

        self.tb = None
        if tensorboard:
            try:
                from torch.utils.tensorboard import SummaryWriter
                self.tb = SummaryWriter(log_dir=str(run_dir / "tensorboard"))
            except ImportError:
                logging.getLogger(__name__).warning(
                    "logging.tensorboard is true but tensorboard is not installed (pip install tensorboard)")

    def log_epoch(self, row: dict[str, Any]) -> None:
        with self.csv_path.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=METRIC_FIELDS).writerow(
                {k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in row.items()})
        if self.tb is not None:
            for key, value in row.items():
                if key != "epoch" and value is not None:
                    self.tb.add_scalar(key, value, row["epoch"])
            self.tb.flush()

    def close(self) -> None:
        if self.tb is not None:
            self.tb.close()

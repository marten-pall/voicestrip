from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless (SSH / tmux)
import matplotlib.pyplot as plt  # noqa: E402
import torch  # noqa: E402


def accuracy(logits: torch.Tensor, targets: torch.Tensor) -> float:
    if targets.numel() == 0:
        return 0.0
    return (logits.argmax(dim=1) == targets).float().mean().item()


def confusion_matrix(preds: torch.Tensor, targets: torch.Tensor, num_classes: int) -> torch.Tensor:
    """[num_classes, num_classes] counts; rows = true label, cols = predicted."""
    idx = targets.long() * num_classes + preds.long()
    return torch.bincount(idx, minlength=num_classes ** 2).reshape(num_classes, num_classes)


def per_class_f1(preds: torch.Tensor, targets: torch.Tensor, labels: list[str]) -> dict[str, dict[str, float]]:
    """{label: {precision, recall, f1, support}}. Classes absent from targets get support 0."""
    cm = confusion_matrix(preds, targets, len(labels)).double()
    tp = cm.diag()
    predicted = cm.sum(dim=0)
    actual = cm.sum(dim=1)
    precision = torch.where(predicted > 0, tp / predicted.clamp_min(1), torch.zeros_like(tp))
    recall = torch.where(actual > 0, tp / actual.clamp_min(1), torch.zeros_like(tp))
    denom = precision + recall
    f1 = torch.where(denom > 0, 2 * precision * recall / denom.clamp_min(1e-12), torch.zeros_like(tp))
    return {
        label: {
            "precision": precision[i].item(),
            "recall": recall[i].item(),
            "f1": f1[i].item(),
            "support": int(actual[i].item()),
        }
        for i, label in enumerate(labels)
    }


def format_report(preds: torch.Tensor, targets: torch.Tensor, labels: list[str], title: str) -> list[str]:
    """Human-readable summary lines (overall accuracy + per-class F1)."""
    acc = (preds == targets).float().mean().item() if targets.numel() else 0.0
    scores = per_class_f1(preds, targets, labels)
    present = [s["f1"] for s in scores.values() if s["support"] > 0]
    macro = sum(present) / len(present) if present else 0.0
    width = max(len(label) for label in labels)
    lines = [
        f"{title}:",
        f"  Overall accuracy: {acc * 100:.1f}%  ({int((preds == targets).sum())}/{targets.numel()})",
        f"  Macro F1 (classes present): {macro:.3f}",
        "  Per-class F1:",
    ]
    for label, s in scores.items():
        if s["support"] == 0:
            lines.append(f"    {label:<{width}}  n/a    (no samples)")
        else:
            lines.append(f"    {label:<{width}}  {s['f1']:.2f}   (P {s['precision']:.2f}  R {s['recall']:.2f}  n={s['support']})")
    return lines


def confusion_matrix_plot(preds: torch.Tensor, targets: torch.Tensor, labels: list[str],
                          save_path: Path, title: str = "Confusion matrix") -> Path:
    """Row-normalised colours, raw counts as annotations."""
    cm = confusion_matrix(preds, targets, len(labels)).double()
    row_sums = cm.sum(dim=1, keepdim=True)
    norm = torch.where(row_sums > 0, cm / row_sums.clamp_min(1), torch.zeros_like(cm))

    n = len(labels)
    fig, ax = plt.subplots(figsize=(0.55 * n + 3, 0.55 * n + 2.5), dpi=120)
    im = ax.imshow(norm.numpy(), cmap="Blues", vmin=0, vmax=1)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="recall (row-normalised)")
    ax.set_xticks(range(n), labels, rotation=45, ha="right")
    ax.set_yticks(range(n), labels)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    acc = (preds == targets).float().mean().item() if targets.numel() else 0.0
    ax.set_title(f"{title}  (acc {acc * 100:.1f}%, n={targets.numel()})")
    for i in range(n):
        for j in range(n):
            count = int(cm[i, j])
            if count:
                ax.text(j, i, str(count), ha="center", va="center", fontsize=8,
                        color="white" if norm[i, j] > 0.5 else "#1e293b")
    fig.tight_layout()
    save_path = Path(save_path)
    fig.savefig(save_path)
    plt.close(fig)
    return save_path

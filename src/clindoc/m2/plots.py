"""plots.py for M2 — reliability diagram, learning curve, severity DISCREPANCY recall."""
from __future__ import annotations

from pathlib import Path


def plot_reliability_diagram(reliability_data: dict, out_path: Path) -> None:
    """Save a reliability diagram PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        bins = reliability_data["bins"]
        avg_conf = [b["avg_conf"] for b in bins if b["count"] > 0]
        avg_acc = [b["avg_acc"] for b in bins if b["count"] > 0]

        fig, ax = plt.subplots(figsize=(6, 6))
        ax.plot([0, 1], [0, 1], "k--", lw=1, label="Perfect calibration")
        ax.bar(
            [b["lo"] for b in bins if b["count"] > 0],
            [b["avg_acc"] for b in bins if b["count"] > 0],
            width=1.0 / len(bins),
            align="edge", alpha=0.7, color="steelblue", label="Accuracy"
        )
        ax.plot(avg_conf, avg_acc, "o-", color="tomato", label="Model")
        ax.set_xlabel("Confidence")
        ax.set_ylabel("Accuracy")
        ax.set_title("Reliability Diagram (M2 Verifier)")
        ax.legend()
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved reliability diagram: {out_path}")
    except Exception as e:
        print(f"Warning: reliability diagram failed: {e}")


def plot_learning_curve(
    fractions: list[float],
    macro_f1_scores: list[float],
    out_path: Path,
) -> None:
    """Save a learning curve PNG."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(7, 5))
        ax.plot(fractions, macro_f1_scores, "o-", color="steelblue", lw=2)
        ax.set_xlabel("Training data fraction")
        ax.set_ylabel("Val Macro-F1")
        ax.set_title("M2 Verifier Learning Curve")
        ax.set_xlim(0, 1.05)
        ax.set_ylim(0, 1.05)
        ax.grid(True, alpha=0.3)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved learning curve: {out_path}")
    except Exception as e:
        print(f"Warning: learning curve failed: {e}")


def plot_severity_discrepancy_recall(
    severities: list[str],
    deterministic_recalls: list[float],
    modernbert_recalls: list[float],
    out_path: Path,
) -> None:
    """Save DISCREPANCY recall by severity for deterministic vs ModernBERT."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        x = np.arange(len(severities))
        width = 0.35
        fig, ax = plt.subplots(figsize=(8, 5))
        bars1 = ax.bar(x - width / 2, deterministic_recalls, width,
                       label="Deterministic verifier", color="steelblue", alpha=0.85)
        bars2 = ax.bar(x + width / 2, modernbert_recalls, width,
                       label="BioClinical ModernBERT", color="tomato", alpha=0.85)
        ax.set_xlabel("Severity")
        ax.set_ylabel("DISCREPANCY Recall")
        ax.set_title("DISCREPANCY Recall by Severity: Deterministic vs ModernBERT")
        ax.set_xticks(x)
        ax.set_xticklabels(severities)
        ax.set_ylim(0, 1.1)
        ax.legend()
        ax.grid(True, axis="y", alpha=0.3)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"Saved severity recall plot: {out_path}")
    except Exception as e:
        print(f"Warning: severity recall plot failed: {e}")

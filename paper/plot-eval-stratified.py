#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


METRIC_LABELS = {
    "block_coverage": "Block coverage",
    "line_coverage": "Line coverage",
    "function_coverage": "Function coverage",
    "file_coverage": "File coverage",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Plot stratified coverage by crash distance and textual anchor."
    )
    parser.add_argument("summary", type=Path, help="Eval summary JSON.")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output figure path.")
    args = parser.parse_args()

    summary = load_json(args.summary)
    stratified = summary["stratified"]
    metric = stratified.get("metric", "block_coverage")
    metric_label = METRIC_LABELS.get(metric, metric.replace("_", " ").title())
    experiments = summary["experiments"]
    exp_ids = [experiment["id"] for experiment in experiments]
    labels = [experiment["short_label"] for experiment in experiments]

    plt.rcParams.update(
        {
            "font.size": 10,
            "axes.titlesize": 11,
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 9.5,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    nrows = len(stratified["rows"])
    ncols = len(stratified["columns"])
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(8.4, 4.8),
        sharex=True,
        sharey=True,
        constrained_layout=False,
    )

    x = np.arange(len(exp_ids))
    cmap = plt.get_cmap("tab20")
    colors = [cmap(i % cmap.N) for i in range(len(exp_ids))]
    handles = None
    for row_idx, row in enumerate(stratified["rows"]):
        for col_idx, col in enumerate(stratified["columns"]):
            ax = axes[row_idx][col_idx]
            cell = stratified["cells"][f"{row['id']}|{col['id']}"]
            bug_count = cell["bug_count"]
            values = [cell["values"][exp_id] for exp_id in exp_ids]
            if "confidence_intervals" not in cell:
                raise ValueError("Missing CIs: run plot/add-eval-confidence-intervals.py first")
            intervals = [cell["confidence_intervals"][exp_id] for exp_id in exp_ids]

            if bug_count:
                bar_values = [0.0 if value is None else value for value in values]

                bars = ax.bar(
                    x,
                    bar_values,
                    width=0.72,
                    color=colors,
                    edgecolor="white",
                    linewidth=0.4,

                )
                # Draw endpoints directly: percentile CIs need not be symmetric
                # or even contain the original point estimate in unusual samples.
                for index, ci in enumerate(intervals):
                    if ci["low"] is not None:
                        ax.vlines(index, ci["low"], ci["high"], color="#333333", linewidth=0.6)
                        ax.hlines([ci["low"], ci["high"]], index - 0.12, index + 0.12,
                                  color="#333333", linewidth=0.6)
                if handles is None:
                    handles = bars
            else:
                ax.set_facecolor("#F1F1F1")
                ax.text(
                    0.5,
                    0.5,
                    "No bugs",
                    transform=ax.transAxes,
                    ha="center",
                    va="center",
                    fontsize=10,
                    color="#555555",
                )

            ax.set_ylim(0.0, 1.0)
            ax.set_yticks([0.0, 0.25, 0.5, 0.75, 1.0])
            ax.grid(axis="y", color="#D0D0D0", linewidth=0.5, alpha=0.8)
            ax.set_axisbelow(True)
            ax.text(
                0.04,
                0.92,
                f"n={bug_count}" + (" (descriptive)" if bug_count == 1 else ""),
                transform=ax.transAxes,
                ha="left",
                va="top",
                fontsize=9.5,
                color="#333333",
                bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 0.4},
            )

            if row_idx == 0:
                ax.set_title(col["label"], pad=6)
            if col_idx == 0:
                ax.set_ylabel(f"{row['label']}\n{metric_label}")
            if row_idx == nrows - 1:
                ax.set_xticks(x)
                ax.set_xticklabels([""] * len(x))
            else:
                ax.set_xticks(x)
                ax.set_xticklabels([])

    if handles is not None:
        fig.legend(
            handles,
            labels,
            loc="lower center",
            ncol=5,
            frameon=False,
            bbox_to_anchor=(0.5, 0.045),
            columnspacing=0.9,
            handlelength=1.1,
            handletextpad=0.4,
        )

    fig.subplots_adjust(left=0.095, right=0.995, top=0.92, bottom=0.25, wspace=0.14, hspace=0.24)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, bbox_inches="tight")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


METRIC_COLUMNS = [
    ("file_coverage", "File cov.", "higher"),
    ("function_coverage", "Func. cov.", "higher"),
    ("line_coverage", "Line cov.", "higher"),
    ("block_coverage", "Block cov.", "higher"),
    ("block_precision", "Block prec.", "higher"),
    ("mention_coverage", "Mention cov.", "higher"),
    ("flaky_bugs", "Flaky bugs", "lower"),
    ("blocks_per_diagnosis", "Blocks/diag.", "lower"),
    ("api_calls", "Calls/run", "lower"),
    ("total_tokens", "Tokens/run", "lower"),
]

TOTAL_COLUMNS = 2 + len(METRIC_COLUMNS)

# Codex CLI reports one call per run, so calls/run is not comparable.
API_CALLS_NOT_APPLICABLE = {
    "codex_cli_gpt_5_2_codex",
    "codex_cli_o4_mini",
}

HARNESS_LABELS = [
    ("mini_swe", "Mini-SWE-Agent"),
    ("codex", "Codex CLI"),
    ("opencode", "OpenCode"),
]

MODEL_LABEL_REWRITES = {
    "M-SWE 5.5": "GPT-5.5",
    "M-SWE 5.4": "GPT-5.4",
    "M-SWE 5.3": "5.3-Codex",
    "M-SWE 5.2": "5.2-Codex",
    "M-SWE HY3": "HY3",
    "M-SWE o4": "o4-mini",
    "M-SWE DS Flash": "DS V4 Flash",
    "M-SWE Kimi": "Kimi K2.6",
    "M-SWE Gemini": "Gemini 3.5",
    "M-SWE MiMo": "MiMo v2.5",
    "M-SWE MiMo Pro": "MiMo v2.5 Pro",
    "M-SWE Astra": "GPT-6 Astra",
    "M-SWE Opus 5.5": "Opus 5.5",
    "Codex 5.2": "5.2-Codex",
    "Codex o4": "o4-mini",
    "OpenCode 5.2": "5.2-Codex",
    "OpenCode o4": "o4-mini",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def latex_escape(value: str) -> str:
    return (
        value.replace("\\", "\\textbackslash{}")
        .replace("&", "\\&")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("#", "\\#")
    )


def harness_label(exp_id: str) -> str:
    for prefix, label in HARNESS_LABELS:
        if exp_id.startswith(prefix):
            return label
    return "Other"


def model_label(experiment: dict) -> str:
    short_label = experiment["short_label"]
    return MODEL_LABEL_REWRITES.get(short_label, short_label)


def metric_values(summary: dict, row_id: str, exp_ids: list[str]) -> list[float | None]:
    if row_id == "block_cov_std":
        return [
            summary["nondeterminism"]["block_coverage"][exp_id]["mean_within_bug_std"]
            for exp_id in exp_ids
        ]
    if row_id == "flaky_bugs":
        return [
            summary["nondeterminism"]["block_coverage"][exp_id]["flaky_rate"]
            for exp_id in exp_ids
        ]
    if row_id == "api_calls":
        return [
            None if exp_id in API_CALLS_NOT_APPLICABLE else summary["metrics"][row_id][exp_id]
            for exp_id in exp_ids
        ]
    return [summary["metrics"][row_id][exp_id] for exp_id in exp_ids]


def best_indices(values: list[float | None], direction: str) -> set[int]:
    numeric = [(idx, value) for idx, value in enumerate(values) if value is not None]
    if not numeric:
        return set()
    best = max(value for _, value in numeric) if direction == "higher" else min(value for _, value in numeric)
    return {idx for idx, value in numeric if abs(value - best) <= 1e-12}


def fmt_percent(value: float | None, bold: bool = False) -> str:
    if value is None:
        return "--"
    rendered = f"{value * 100:.1f}"
    return f"\\textbf{{{rendered}}}" if bold else rendered


def fmt_api(value: float | None, bold: bool = False) -> str:
    if value is None:
        return "N/A"
    rendered = f"{value:.1f}"
    return f"\\textbf{{{rendered}}}" if bold else rendered


def fmt_count(value: float | None, bold: bool = False) -> str:
    if value is None:
        return "--"
    rendered = f"{value:.1f}"
    return f"\\textbf{{{rendered}}}" if bold else rendered


def fmt_tokens(value: float | None, bold: bool = False) -> str:
    if value is None:
        return "--"
    rendered = f"{value / 1000:.0f}K"
    return f"\\textbf{{{rendered}}}" if bold else rendered


def fmt_flaky(summary: dict, exp_id: str, bold: bool = False) -> str:
    nondet = summary["nondeterminism"]["block_coverage"][exp_id]
    rendered = f"{nondet['flaky_bug_count']} ({nondet['flaky_rate'] * 100:.0f}\\%)"
    return f"\\textbf{{{rendered}}}" if bold else rendered


def fmt_block_ci(summary: dict, exp_id: str) -> str:
    ci = summary["confidence_intervals"]["block_coverage"][exp_id]
    if ci.get("low") is None or ci.get("high") is None:
        return ""
    return rf"\,{{\tiny[{100 * ci['low']:.1f}, {100 * ci['high']:.1f}]}}"


def render_value(summary: dict, row_id: str, exp_id: str, value: float | None, bold: bool) -> str:
    if row_id == "block_coverage":
        return fmt_percent(value, bold) + fmt_block_ci(summary, exp_id)
    if row_id == "api_calls":
        return fmt_api(value, bold)
    if row_id == "blocks_per_diagnosis":
        return fmt_count(value, bold)
    if row_id == "total_tokens":
        return fmt_tokens(value, bold)
    if row_id == "flaky_bugs":
        return fmt_flaky(summary, exp_id, bold)
    return fmt_percent(value, bold)


def experiment_group(exp_id: str) -> str:
    if exp_id.startswith("mini_swe"):
        return "Mini-SWE-Agent model sweep"
    return "Harness comparison"


def row_prefix(is_top_block: bool) -> str:
    # Shade the configuration with the highest block coverage (primary metric).
    if is_top_block:
        return r"\rowcolor{evaltoprow}"
    return ""


def render_rows(summary: dict, experiments: list[dict]) -> list[str]:
    exp_ids = [experiment["id"] for experiment in experiments]
    best_by_metric = {
        row_id: best_indices(metric_values(summary, row_id, exp_ids), direction)
        for row_id, _label, direction in METRIC_COLUMNS
    }
    values_by_metric = {
        row_id: metric_values(summary, row_id, exp_ids)
        for row_id, _label, _direction in METRIC_COLUMNS
    }
    block_values = values_by_metric["block_coverage"]
    top_block_idx = max(range(len(exp_ids)), key=lambda idx: block_values[idx])
    rendered_rows = []
    current_group = None
    for exp_idx, experiment in enumerate(experiments):
        exp_id = experiment["id"]
        group = experiment_group(exp_id)
        if group != current_group:
            if current_group is not None:
                rendered_rows.append(r"    \addlinespace[0.25ex]")
            rendered_rows.append(
                rf"    \rowcolor{{evalgroupbg}}\multicolumn{{{TOTAL_COLUMNS}}}{{@{{}}l@{{}}}}{{\textit{{{group}}}}} \\"
            )
            current_group = group
        cells = []
        for row_id, _label, _direction in METRIC_COLUMNS:
            value = values_by_metric[row_id][exp_idx]
            cells.append(render_value(summary, row_id, exp_id, value, exp_idx in best_by_metric[row_id]))
        prefix = row_prefix(exp_idx == top_block_idx)
        prefix = f"{prefix} " if prefix else ""
        rendered_rows.append(
            f"    {prefix}{latex_escape(model_label(experiment))} & {latex_escape(harness_label(exp_id))} & "
            + " & ".join(cells)
            + r" \\"
        )
    return rendered_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the main eval results table.")
    parser.add_argument("summary", type=Path, help="Eval summary JSON.")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output .tex path.")
    args = parser.parse_args()

    summary = load_json(args.summary)
    experiments = summary["experiments"]
    bug_count = summary["metadata"]["common_bug_count"]

    lines = [
        r"\begin{table*}[t]",
        r"  \centering",
        r"  \scriptsize",
        r"  \definecolor{evalmaincol}{RGB}{239,246,255}",
        r"  \definecolor{evaltoprow}{RGB}{248,251,255}",
        r"  \definecolor{evalgroupbg}{RGB}{238,241,245}",
        r"  \setlength{\tabcolsep}{3.4pt}",
        r"  \renewcommand{\arraystretch}{1.06}",
        rf"  \caption{{Main evaluation results across the {bug_count} common \bench incidents. "
        r"The shaded column is the primary localization metric, with 95\% bug-bootstrap CIs in brackets; bold values are column bests. \Cref{tab:eval-confidence-intervals} gives CIs for the other metrics.}",
        r"  \label{tab:eval}",
        r"  \resizebox{\textwidth}{!}{%",
        r"  \begin{tabular}{@{}ll@{\hspace{6pt}}rrr>{\columncolor{evalmaincol}}r@{\hspace{6pt}}rr@{\hspace{6pt}}r@{\hspace{6pt}}rrr@{}}",
        r"    \toprule",
        r"    \multicolumn{2}{@{}l}{\textbf{Configuration}} & "
        r"\multicolumn{4}{c}{\textbf{Localization coverage}} & "
        r"\multicolumn{2}{c}{\textbf{Explanation}} & "
        r"\multicolumn{1}{c}{\textbf{Instability}} & "
        r"\multicolumn{3}{c@{}}{\textbf{Effort}} \\",
        r"    \cmidrule(lr){1-2}\cmidrule(lr){3-6}\cmidrule(lr){7-8}\cmidrule(lr){9-9}\cmidrule(l){10-12}",
        r"    Model & Harness & File & Func. & Line & Block & Block prec. & Mention & Flaky bugs & Blocks/diag. & Calls/run & Tokens/run \\",
        r"    \midrule",
    ]
    lines.extend(render_rows(summary, experiments))
    lines.extend(
        [
            r"    \bottomrule",
            r"  \end{tabular}",
            r"  }",
            r"  \vspace{0.02in}",
            r"  \begin{minipage}{0.98\textwidth}",
            rf"  \footnotesize\emph{{Note.}} Values are averaged over {bug_count} common bugs, after averaging the first three valid scored attempts within each bug. "
            r"Coverage, block precision, mention coverage, and flaky-bug rates are percentages. "
            r"\Cref{app:eval-attempt-counts} details attempt selection and the common cohort. "
            r"Lower is better for flaky bugs, blocks/diagnosis, calls/run, and tokens/run. "
            r"Flaky bugs have block-coverage spread above 0.2 across attempts. "
            r"5.3-Codex and 5.2-Codex abbreviate GPT-5.3-Codex and GPT-5.2-Codex.",
            r"  \end{minipage}",
            r"\end{table*}",
            "",
        ]
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()

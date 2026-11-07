#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from statistics import mean


MAIN_METRICS = [
    "file_coverage",
    "function_coverage",
    "line_coverage",
    "file_precision",
    "function_precision",
    "line_precision",
    "block_coverage",
    "block_precision",
    "mention_coverage",
    "blocks_per_diagnosis",
    "api_calls",
    "total_tokens",
]

NONDET_METRICS = ["block_coverage"]

CRASH_DISTANCE_ORDER = [
    "local_or_adjacent",
    "cross_component",
    "no_stack_trace",
]

TEXTUAL_ANCHOR_ORDER = ["method", "file_class", "opaque"]

CRASH_DISTANCE_LABELS = {
    "local_or_adjacent": "Local/adjacent",
    "cross_component": "Cross-component",
    "no_stack_trace": "No stack trace",
}

CRASH_DISTANCE_GROUPS = {
    "local_or_adjacent": {"co_located", "adjacent"},
    "cross_component": {"cross_component"},
    "no_stack_trace": {"no_stack_trace"},
}

TEXTUAL_ANCHOR_LABELS = {
    "method": "Exact method",
    "file_class": "File/class",
    "opaque": "Opaque",
}

# Keyed by the configuration names in the traces dataset, in Table 1 order.
EXPERIMENT_LABELS = {
    "mini-swe-agent__gpt-6-astra": {
        "label": "MiniSWE GPT-6 Astra",
        "short_label": "M-SWE Astra",
    },
    "mini-swe-agent__claude-opus-5.5": {
        "label": "MiniSWE Claude Opus 5.5",
        "short_label": "M-SWE Opus 5.5",
    },
    "mini-swe-agent__gpt-5.5": {
        "label": "MiniSWE GPT-5.5",
        "short_label": "M-SWE 5.5",
    },
    "mini-swe-agent__gpt-5.4": {
        "label": "MiniSWE GPT-5.4",
        "short_label": "M-SWE 5.4",
    },
    "mini-swe-agent__gpt-5.3-codex": {
        "label": "MiniSWE GPT-5.3-Codex",
        "short_label": "M-SWE 5.3",
    },
    "mini-swe-agent__kimi-k2.6": {
        "label": "MiniSWE Kimi K2.6",
        "short_label": "M-SWE Kimi",
    },
    "mini-swe-agent__mimo-v2.5-pro": {
        "label": "MiniSWE MiMo v2.5 Pro",
        "short_label": "M-SWE MiMo Pro",
    },
    "mini-swe-agent__mimo-v2.5": {
        "label": "MiniSWE MiMo v2.5",
        "short_label": "M-SWE MiMo",
    },
    "mini-swe-agent__deepseek-v4-flash": {
        "label": "MiniSWE DeepSeek V4 Flash",
        "short_label": "M-SWE DS Flash",
    },
    "mini-swe-agent__gpt-5.2-codex": {
        "label": "MiniSWE GPT-5.2-Codex",
        "short_label": "M-SWE 5.2",
    },
    "mini-swe-agent__hy3": {
        "label": "MiniSWE HY3",
        "short_label": "M-SWE HY3",
    },
    "mini-swe-agent__gemini-3.5-flash": {
        "label": "MiniSWE Gemini 3.5 Flash",
        "short_label": "M-SWE Gemini",
    },
    "mini-swe-agent__o4-mini": {
        "label": "MiniSWE o4-mini",
        "short_label": "M-SWE o4",
    },
    "codex-cli__gpt-5.2-codex": {
        "label": "Codex CLI GPT-5.2-Codex",
        "short_label": "Codex 5.2",
    },
    "codex-cli__o4-mini": {
        "label": "Codex CLI o4-mini",
        "short_label": "Codex o4",
    },
    "opencode__gpt-5.2-codex": {
        "label": "OpenCode GPT-5.2-Codex",
        "short_label": "OpenCode 5.2",
    },
    "opencode__o4-mini": {
        "label": "OpenCode o4-mini",
        "short_label": "OpenCode o4",
    },
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def experiment_json_path(path: Path) -> Path:
    if path.is_dir():
        return path / "aggregated_evaluations.json"
    return path


def stat_mean(stats_entry) -> float | None:
    if isinstance(stats_entry, dict):
        if "mean" in stats_entry:
            return float(stats_entry["mean"])
        return None
    if isinstance(stats_entry, (int, float)):
        return float(stats_entry)
    return None


def stat_values(stats_entry) -> list[float]:
    if isinstance(stats_entry, dict):
        return [float(v) for v in stats_entry.get("values", [])]
    return []


def compute_stats(values: list[float]) -> dict:
    if not values:
        return {"mean": None, "std": None, "min": None, "max": None, "values": []}
    avg = mean(values)
    variance = mean([(value - avg) ** 2 for value in values])
    return {
        "mean": avg,
        "std": variance**0.5,
        "min": min(values),
        "max": max(values),
        "values": values,
    }


def diagnosis_blocks(payload: object) -> list:
    if isinstance(payload, dict):
        if isinstance(payload.get("diagnosis"), dict):
            return diagnosis_blocks(payload["diagnosis"])
        blocks = payload.get("buggy_blocks")
        if blocks is None:
            blocks = payload.get("buggy_code_blocks")
        return blocks if isinstance(blocks, list) else []
    if isinstance(payload, list):
        return payload
    return []


def diagnosis_block_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return len(diagnosis_blocks(load_json(path)))
    except json.JSONDecodeError:
        return 0


def add_derived_metrics(data: dict, experiment: dict) -> None:
    experiment_root = Path(experiment["aggregated_evaluations"]).parent
    for bug, bug_data in data.items():
        values = []
        for run in bug_data.get("runs", []):
            if not isinstance(run, dict):
                continue
            run_name = run.get("run")
            if not isinstance(run_name, str):
                continue
            values.append(float(diagnosis_block_count(experiment_root / bug / run_name / "diagnosis.json")))
        if values:
            bug_data.setdefault("stats", {})["blocks_per_diagnosis"] = compute_stats(values)


def metric_average(data: dict, bugs: set[str], metric: str) -> float | None:
    values = []
    for bug in bugs:
        metric_stats = data.get(bug, {}).get("stats", {}).get(metric)
        value = stat_mean(metric_stats)
        if value is not None:
            values.append(value)
    return mean(values) if values else None


def nondeterminism_summary(data: dict, bugs: set[str], metric: str) -> dict:
    stds = []
    spreads = []
    identical_count = 0
    flaky_count = 0
    bug_count = 0

    for bug in bugs:
        metric_stats = data.get(bug, {}).get("stats", {}).get(metric)
        values = stat_values(metric_stats)
        if not values:
            continue

        bug_count += 1
        spread = max(values) - min(values)
        std = float(metric_stats.get("std", 0.0)) if isinstance(metric_stats, dict) else 0.0
        stds.append(std)
        spreads.append(spread)
        if spread <= 1e-12:
            identical_count += 1
        if spread > 0.2:
            flaky_count += 1

    return {
        "bug_count": bug_count,
        "mean_within_bug_std": mean(stds) if stds else 0.0,
        "mean_spread": mean(spreads) if spreads else 0.0,
        "identical_bug_count": identical_count,
        "identical_rate": identical_count / bug_count if bug_count else 0.0,
        "flaky_bug_count": flaky_count,
        "flaky_rate": flaky_count / bug_count if bug_count else 0.0,
    }


def label_for_experiment(path: Path) -> dict:
    name = path.name if path.is_dir() else path.parent.name
    configured = EXPERIMENT_LABELS.get(name, {})
    return {
        "id": slugify(name),
        "name": name,
        "label": configured.get("label", name),
        "short_label": configured.get("short_label", name),
        "path": str(path),
        "aggregated_evaluations": str(experiment_json_path(path)),
    }


def build_stratified_summary(
    exp_data: dict[str, dict],
    exp_ids: list[str],
    common_bugs: set[str],
    difficulty: dict,
    metric: str,
) -> dict:
    cells = {}
    for distance in CRASH_DISTANCE_ORDER:
        for anchor in TEXTUAL_ANCHOR_ORDER:
            bugs = sorted(
                bug
                for bug in common_bugs
                if difficulty.get(bug, {}).get("crash_to_fix_distance")
                in CRASH_DISTANCE_GROUPS[distance]
                and difficulty.get(bug, {}).get("textual_anchor") == anchor
            )
            values = {}
            mean_stds = {}
            for exp_id in exp_ids:
                exp_values = []
                exp_stds = []
                for bug in bugs:
                    metric_stats = exp_data[exp_id].get(bug, {}).get("stats", {}).get(metric)
                    value = stat_mean(metric_stats)
                    if value is not None:
                        exp_values.append(value)
                    if isinstance(metric_stats, dict) and "std" in metric_stats:
                        exp_stds.append(float(metric_stats["std"]))
                values[exp_id] = mean(exp_values) if exp_values else None
                mean_stds[exp_id] = mean(exp_stds) if exp_stds else None

            cells[f"{distance}|{anchor}"] = {
                "bug_count": len(bugs),
                "bug_ids": bugs,
                "values": values,
                "mean_within_bug_std": mean_stds,
            }

    return {
        "metric": metric,
        "rows": [
            {"id": value, "label": CRASH_DISTANCE_LABELS[value]}
            for value in CRASH_DISTANCE_ORDER
        ],
        "columns": [
            {"id": value, "label": TEXTUAL_ANCHOR_LABELS[value]}
            for value in TEXTUAL_ANCHOR_ORDER
        ],
        "cells": cells,
    }


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    default_difficulty = repo_root / "benchmark" / "bug_difficulty_characterizations.json"

    parser = argparse.ArgumentParser(
        description="Compact experiment aggregate files into paper eval data."
    )
    parser.add_argument(
        "--experiments",
        nargs="+",
        required=True,
        type=Path,
        help="Experiment directories or aggregated_evaluations.json files.",
    )
    parser.add_argument(
        "--difficulty",
        type=Path,
        default=default_difficulty,
        help="Bug difficulty characterization JSON.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        required=True,
        help="Output summary JSON path.",
    )
    parser.add_argument(
        "--stratified-metric",
        default="block_coverage",
        help="Metric used for the stratified figure.",
    )
    parser.add_argument("--cohort-from", type=Path,
                        help="Freeze bug IDs from an existing summary (recommended for new models)")
    parser.add_argument("--allow-variable-attempts", action="store_true")
    parser.add_argument("--first-three-valid", action="store_true",
                        help="Select first three valid scored run IDs and intersect complete cohorts")
    parser.add_argument("--selection-audit", type=Path,
                        help="Write run selection, skipped artifacts, and excluded bugs")
    parser.add_argument("--draft-allow-incomplete", action="store_true",
                        help="Provisional frozen-cohort draft; retain existing scores without imputing missing attempts")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--resamples", type=int, default=10000)
    args = parser.parse_args()
    if args.draft_allow_incomplete and not (args.first_three_valid and args.cohort_from):
        parser.error("--draft-allow-incomplete requires --first-three-valid and --cohort-from")

    experiments = [label_for_experiment(path) for path in args.experiments]
    exp_data = {}
    selection_audit = {"policy": "first three schema-valid scored attempts in numeric run order",
                       "attempts_per_bug": 3, "experiments": {}}
    candidate_bugs = set()
    for experiment in experiments:
        json_path = Path(experiment["aggregated_evaluations"])
        if not json_path.exists():
            raise FileNotFoundError(json_path)
        exp_data[experiment["id"]] = load_json(json_path)
        candidate_bugs.update(exp_data[experiment["id"]])
        if args.first_three_valid:
            from eval_attempt_selection import select_attempts
            exp_data[experiment["id"]], audit = select_attempts(
                exp_data[experiment["id"]], json_path.parent, allow_incomplete=args.draft_allow_incomplete)
            selection_audit["experiments"][experiment["id"]] = audit
        else:
            add_derived_metrics(exp_data[experiment["id"]], experiment)

    bug_sets = [set(data) for data in exp_data.values()]
    if args.cohort_from:
        common_bugs = set(load_json(args.cohort_from)["metadata"]["common_bug_ids"])
        for experiment, available in zip(experiments, bug_sets):
            missing = common_bugs - available
            if missing:
                raise ValueError(f"{experiment['id']}: missing frozen-cohort bugs: {sorted(missing)}")
    elif args.first_three_valid:
        common_bugs = set.intersection(*bug_sets)
    else:
        common_bugs = bug_sets[0]
        if any(bugs != common_bugs for bugs in bug_sets[1:]):
            raise ValueError("Experiment cohorts differ; supply --cohort-from rather than silently intersecting")
    if not common_bugs:
        raise ValueError("No common bugs across experiments.")

    metrics = {}
    for metric in MAIN_METRICS:
        metrics[metric] = {
            experiment["id"]: metric_average(exp_data[experiment["id"]], common_bugs, metric)
            for experiment in experiments
        }

    nondeterminism = {}
    for metric in NONDET_METRICS:
        nondeterminism[metric] = {
            experiment["id"]: nondeterminism_summary(
                exp_data[experiment["id"]], common_bugs, metric
            )
            for experiment in experiments
        }

    difficulty = load_json(args.difficulty)
    missing_difficulty = sorted(common_bugs - set(difficulty))
    if missing_difficulty:
        raise ValueError(
            f"Difficulty metadata missing {len(missing_difficulty)} bugs: "
            f"{missing_difficulty[:5]}"
        )

    summary = {
        "metadata": {
            "score_source_sha256": {
                exp["id"]: hashlib.sha256(Path(exp["aggregated_evaluations"]).read_bytes()).hexdigest()
                for exp in experiments},
            "common_bug_count": len(common_bugs),
            "common_bug_ids": sorted(common_bugs),
            "source_experiment_count": len(experiments),
            "difficulty_path": str(args.difficulty),
        },
        "experiments": experiments,
        "metrics": metrics,
        "nondeterminism": nondeterminism,
        "stratified": build_stratified_summary(
            exp_data,
            [experiment["id"] for experiment in experiments],
            common_bugs,
            difficulty,
            args.stratified_metric,
        ),
    }

    from eval_confidence import extract_attempts, add_confidence_intervals
    if args.first_three_valid:
        selection_audit["candidate_bug_ids"] = sorted(candidate_bugs)
        selection_audit["common_bug_ids"] = sorted(common_bugs)
        selection_audit["excluded_bug_ids"] = sorted(candidate_bugs - common_bugs)
        pending = {exp: {bug: 3 - n for bug, n in audit["insufficient_bugs"].items()
                        if bug in common_bugs} for exp, audit in selection_audit["experiments"].items()}
        selection_audit["pending_attempts"] = {exp: bugs for exp, bugs in pending.items() if bugs}
        selection_audit["status"] = "provisional_missing_attempts" if any(pending.values()) else "complete"
        if any(pending.values()):
            selection_audit["policy"] = "first up to three schema-valid scored attempts in numeric run order; provisional, no imputation"
        summary["metadata"]["attempt_selection"] = selection_audit
        if args.selection_audit:
            args.selection_audit.parent.mkdir(parents=True, exist_ok=True)
            args.selection_audit.write_text(json.dumps(selection_audit, indent=2, sort_keys=True) + "\n")
    scores = extract_attempts(summary, exp_data,
                              allow_variable_attempts=args.allow_variable_attempts or args.draft_allow_incomplete)
    add_confidence_intervals(summary, scores, seed=args.seed, resamples=args.resamples)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {args.output} for {len(common_bugs)} common bugs.")


if __name__ == "__main__":
    main()

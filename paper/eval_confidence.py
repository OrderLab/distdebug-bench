"""Bug-level percentile bootstrap; attempts stay together within each bug."""
from __future__ import annotations

import math
import numpy as np

CI_METRICS = (
    "file_coverage", "function_coverage", "line_coverage", "block_coverage",
    "file_precision", "function_precision", "line_precision", "block_precision",
    "mention_coverage",
)
DEFAULT_SEED = 2026
DEFAULT_RESAMPLES = 10_000


def bootstrap_mean(values, *, seed=DEFAULT_SEED, resamples=DEFAULT_RESAMPLES):
    """95% percentile CI for equal-weight mean of independent bug means.

    A fresh generator and a fixed sorted bug order make each result independent
    of model order. Single-bug cells have a point estimate but no interval.
    """
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not np.all(np.isfinite(x)):
        raise ValueError("Bug means must be a finite one-dimensional vector")
    if not isinstance(resamples, int) or resamples < 2:
        raise ValueError("resamples must be an integer >= 2")
    if not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    n = len(x)
    result = {"mean": float(x.mean()) if n else None, "n_bugs": n,
              "low": None, "high": None,
              "status": "ok" if n >= 2 else "insufficient_bugs"}
    if n >= 2:
        rng = np.random.Generator(np.random.PCG64(seed))
        means = np.empty(resamples)
        # Bound memory without changing RNG draw ordering.
        for start in range(0, resamples, 1000):
            count = min(1000, resamples - start)
            indices = rng.integers(0, n, size=(count, n))
            means[start:start + count] = x[indices].mean(axis=1)
        result["low"], result["high"] = map(
            float, np.quantile(means, [0.025, 0.975], method="linear"))
    return result


def paired_difference(candidate, reference, *, seed=DEFAULT_SEED,
                      resamples=DEFAULT_RESAMPLES):
    """Bootstrap candidate minus reference, pairing bug means by ID."""
    if not candidate or set(candidate) != set(reference):
        raise ValueError("Paired models must have the same nonempty bug cohort")
    bugs = sorted(candidate)
    a = np.asarray([candidate[bug] for bug in bugs], dtype=float)
    b = np.asarray([reference[bug] for bug in bugs], dtype=float)
    for values in (a, b):
        if values.ndim != 1 or not np.all(np.isfinite(values)) or np.any((values < 0) | (values > 1)):
            raise ValueError("Per-bug accuracy means must be finite values in [0, 1]")
    result = bootstrap_mean(a - b, seed=seed, resamples=resamples)
    result.update(candidate_mean=float(a.mean()), reference_mean=float(b.mean()))
    return result


def extract_attempts(summary, exp_data, *, allow_variable_attempts=False):
    """Validate raw per-bug accuracy vectors, retaining no diagnosis text."""
    bugs = summary["metadata"]["common_bug_ids"]
    if not bugs or len(bugs) != len(set(bugs)):
        raise ValueError("Cohort must contain unique bug IDs")
    scores = {}
    for experiment in summary["experiments"]:
        exp_id = experiment["id"]
        data = exp_data[exp_id]
        missing = set(bugs) - set(data)
        if missing:
            raise ValueError(f"{exp_id}: missing cohort bugs: {sorted(missing)}")
        scores[exp_id] = {}
        for bug in sorted(bugs):
            stats = data[bug].get("stats", {})
            entry = {}
            for metric in CI_METRICS:
                stat = stats.get(metric, {})
                values = stat.get("values", []) if isinstance(stat, dict) else []
                if not values or any(isinstance(v, bool) or not isinstance(v, (int, float))
                                     or not math.isfinite(v) or not 0 <= v <= 1 for v in values):
                    raise ValueError(f"{exp_id}/{bug}/{metric}: missing or invalid attempt scores")
                if not allow_variable_attempts and len(values) != 3:
                    raise ValueError(f"{exp_id}/{bug}: expected 3 attempts, found {len(values)}; "
                                     "audit selection or explicitly use --allow-variable-attempts")
                if not math.isclose(float(np.mean(values)), stat.get("mean", float("nan")),
                                    abs_tol=1e-10):
                    raise ValueError(f"{exp_id}/{bug}/{metric}: recorded mean differs from attempts")
                entry[metric] = values
            if len({len(values) for values in entry.values()}) != 1:
                raise ValueError(f"{exp_id}/{bug}: inconsistent attempt counts between metrics")
            scores[exp_id][bug] = entry
    return scores


def add_confidence_intervals(summary, scores, *, seed=DEFAULT_SEED,
                             resamples=DEFAULT_RESAMPLES):
    bugs = sorted(summary["metadata"]["common_bug_ids"])
    exp_ids = [exp["id"] for exp in summary["experiments"]]
    if len(exp_ids) != len(set(exp_ids)):
        raise ValueError("Duplicate experiment IDs")
    if len(bugs) != len(set(bugs)) or len(bugs) != summary["metadata"]["common_bug_count"]:
        raise ValueError("Invalid cohort metadata")
    # Revalidate portable vectors using the same path as raw inputs.
    raw = {exp: {bug: {"stats": {metric: {"values": values, "mean": float(np.mean(values))}
            for metric, values in entry.items()}} for bug, entry in entries.items()}
            for exp, entries in scores.items()}
    scores = extract_attempts(summary, raw, allow_variable_attempts=True)
    means = {metric: {exp: {bug: float(np.mean(scores[exp][bug][metric]))
             for bug in bugs} for exp in exp_ids} for metric in CI_METRICS}
    intervals = {}
    for metric in CI_METRICS:
        intervals[metric] = {}
        for exp in exp_ids:
            ci = bootstrap_mean([means[metric][exp][bug] for bug in bugs], seed=seed, resamples=resamples)
            recorded = summary["metrics"][metric][exp]
            if recorded is None or not math.isclose(ci["mean"], recorded, abs_tol=1e-10):
                raise ValueError(f"{exp}/{metric}: source scores do not reproduce summary mean")
            intervals[metric][exp] = ci
    metric = summary["stratified"]["metric"]
    covered = []
    for key, cell in summary["stratified"]["cells"].items():
        cell_bugs = sorted(cell["bug_ids"])
        if len(cell_bugs) != cell["bug_count"] or not set(cell_bugs) <= set(bugs):
            raise ValueError(f"Invalid cohort for cell {key}")
        covered.extend(cell_bugs)
        cell["confidence_intervals"] = {}
        for exp in exp_ids:
            ci = bootstrap_mean([means[metric][exp][bug] for bug in cell_bugs], seed=seed, resamples=resamples)
            recorded = cell["values"][exp]
            if ci["mean"] is not None and (recorded is None or not math.isclose(ci["mean"], recorded, abs_tol=1e-10)):
                raise ValueError(f"{exp}/{key}: source scores do not reproduce cell mean")
            cell["confidence_intervals"][exp] = ci
    if sorted(covered) != bugs:
        raise ValueError("Stratified cells must partition the full cohort exactly once")
    counts = {exp: {bug: len(scores[exp][bug]["block_coverage"]) for bug in bugs} for exp in exp_ids}
    summary["metadata"]["schema_version"] = 2
    summary["metadata"]["confidence_interval"] = {
        "method": "bug-level percentile bootstrap", "confidence_level": 0.95,
        "resamples": resamples, "seed": seed, "rng": "numpy.random.PCG64",
        "numpy_version": np.__version__, "quantile_method": "linear",
        "resampling_unit": "bug", "aggregation": "mean of per-bug attempt means",
        "attempt_policy": summary["metadata"].get("attempt_selection", {}).get(
            "policy", "all supplied attempts retained within each bug"),
        "singleton_policy": "no interval for fewer than two bugs",
    }
    summary["metadata"]["attempt_counts"] = counts
    summary["metadata"]["non_three_attempt_bugs"] = {
        exp: {bug: n for bug, n in values.items() if n != 3} for exp, values in counts.items()
        if any(n != 3 for n in values.values())}
    summary["per_bug_attempt_scores"] = scores
    summary["confidence_intervals"] = intervals
    return summary

"""Select first three schema-valid, scored attempts without using their scores."""
import hashlib
import json
import math
import re
from pathlib import Path
from statistics import mean, pstdev

from eval_confidence import CI_METRICS


def run_number(name):
    match = re.fullmatch(r"run-(\d+)", name)
    if not match:
        raise ValueError(f"Unrecognized attempt ID: {name}")
    return int(match.group(1))


def valid_diagnosis(payload):
    # Empty predictions are valid completed answers, including zero-score failures.
    if not isinstance(payload, dict) or not isinstance(payload.get("bug_cause_description"), str):
        return False
    blocks = payload.get("buggy_blocks")
    if not isinstance(blocks, list):
        return False
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("file"), str) or not isinstance(block.get("function"), str):
            return False
        for key in ("start_line", "end_line"):
            value = block.get(key)
            if isinstance(value, bool):
                return False
            try:
                int(value)
            except (ValueError, TypeError):
                return False
    return True


def select_attempts(data, root, count=3, *, allow_incomplete=False):
    """Return reaggregated bugs and an audit; require disk scores to match aggregates.

    Incomplete/malformed artifacts are skipped. Conflicting completed artifacts
    abort import instead of silently excluding a low-scoring or stale record.
    """
    root = Path(root)
    selected, audit = {}, {"selected_run_ids": {}, "extra_valid_run_ids": {},
                           "skipped_runs": {}, "insufficient_bugs": {}}
    source_hash = hashlib.sha256()
    for bug, entry in sorted(data.items()):
        rows = entry.get("runs", [])
        by_run = {r["run"]: r for r in rows}
        if len(by_run) != len(rows):
            raise ValueError(f"Duplicate run IDs: {root.name}/{bug}")
        directories = {p.name for p in (root / bug).glob("run-*") if p.is_dir()}
        good, skipped = [], {}
        for name in sorted(set(by_run) | directories, key=run_number):
            path = root / bug / name
            artifacts = {}
            for filename in ("diagnosis.json", "diagnosis_scores.json"):
                file = path / filename
                if not file.exists():
                    skipped[name] = "missing diagnosis or score artifact"
                    break
                raw = file.read_bytes()
                source_hash.update(f"{bug}/{name}/{filename}\0".encode() + raw)
                try:
                    artifacts[filename] = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    skipped[name] = "unparseable diagnosis or score artifact"
                    break
            if name in skipped:
                continue
            diag, scored = artifacts["diagnosis.json"], artifacts["diagnosis_scores.json"]
            if not valid_diagnosis(diag):
                skipped[name] = "invalid diagnosis schema"
                continue
            if name not in by_run:
                raise ValueError(f"Completed attempt absent from aggregate: {path}")
            row = dict(by_run[name])
            # The evaluator's BugDiagnosis serialization omits proposed patches.
            evaluated_diag = {**diag, "buggy_blocks": [
                {k: v for k, v in block.items() if k != "patch"} for block in diag["buggy_blocks"]]}
            if scored.get("bug") != bug or scored.get("run") != name or scored.get("diagnosis") not in (diag, evaluated_diag):
                raise ValueError(f"Diagnosis/score identity mismatch: {path}")
            for metric in CI_METRICS:
                value = scored.get("scores", {}).get(metric)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError(f"Incomplete/invalid score {metric}: {path}")
                if metric not in row or not math.isclose(value, row[metric], rel_tol=0, abs_tol=1e-10):
                    raise ValueError(f"Aggregate/score mismatch {metric}: {path}")
            row["blocks_per_diagnosis"] = len(diag["buggy_blocks"])
            good.append(row)
        if skipped:
            audit["skipped_runs"][bug] = skipped
        audit["selected_run_ids"][bug] = [r["run"] for r in good[:count]]
        if len(good) < count:
            audit["insufficient_bugs"][bug] = len(good)
            if not allow_incomplete or not good:
                continue
        if len(good) > count:
            audit["extra_valid_run_ids"][bug] = [r["run"] for r in good[count:]]
        chosen = good[:count]
        metrics = set(CI_METRICS) | {"blocks_per_diagnosis", "api_calls", "total_tokens", "duration_seconds"}
        stats = {}
        for metric in sorted(metrics):
            values = [r[metric] for r in chosen if r.get(metric) is not None]
            if values and len(values) != len(chosen):
                raise ValueError(f"Partially missing effort metric {metric}: {root.name}/{bug}")
            if values:
                if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
                    raise ValueError(f"Invalid metric {metric}: {root.name}/{bug}")
                stats[metric] = {"mean": mean(values), "std": pstdev(values),
                                 "min": min(values), "max": max(values), "values": values}
        selected[bug] = {"runs": chosen, "num_runs": len(chosen), "stats": stats}
    audit["artifact_sha256"] = source_hash.hexdigest()
    return selected, audit

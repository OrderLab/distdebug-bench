"""Reproduce the paper's Table 1 and Figures 2-3 from the released traces.

From the repository root:

    hf download OrderLab/distdebug-bench-traces --repo-type dataset --local-dir traces
    python paper/reproduce.py traces

The script unpacks each configuration, rescores every attempt's diagnosis with
`distdebug_bench.score`, aggregates the scores with the paper's scripts in this
directory, and compares the result with `paper/reference/`. Outputs go to
`paper/out/`: eval_summary.json, eval_main_results.tex (Table 1),
bench_characterization.pdf (Figure 2), and
eval_stratified_block_coverage.pdf (Figure 3).
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tarfile
from pathlib import Path

PAPER = Path(__file__).resolve().parent
REPO = PAPER.parent
sys.path.insert(0, str(REPO))

from distdebug_bench.benchmark import load_benchmark  # noqa: E402
from distdebug_bench.score import _run_dirs, score_run  # noqa: E402

EFFORT = (
    "api_calls", "input_tokens", "input_cached_tokens", "output_tokens",
    "output_reasoning_tokens", "total_tokens", "cost", "duration_seconds",
)
# Recorded by the local run or this machine rather than by the experiments.
IGNORED = {
    ("metadata", "difficulty_path"),
    ("metadata", "score_source_sha256"),
    ("metadata", "attempt_selection"),
    ("metadata", "confidence_interval", "numpy_version"),
    ("experiments", "*", "path"),
    ("experiments", "*", "aggregated_evaluations"),
}


def unpack(traces: Path, runs: Path, configs: list[dict]) -> None:
    runs.mkdir(parents=True, exist_ok=True)
    for cfg in configs:
        if (runs / cfg["config"]).exists():
            continue
        with tarfile.open(traces / "runs" / f"{cfg['config']}.tar.gz") as tar:
            tar.extractall(runs, filter="data")


def rescore(runs: Path, configs: list[dict]) -> None:
    bugs = {bug.name: bug for bug in load_benchmark(REPO).iter_bugs("all")}
    for cfg in configs:
        for bug_dir in sorted(p for p in (runs / cfg["config"]).iterdir() if p.is_dir()):
            for run_dir in _run_dirs(bug_dir):
                if score_run(bugs[bug_dir.name], run_dir) is None:
                    raise SystemExit(f"invalid diagnosis: {run_dir}")


def write_aggregates(runs: Path, configs: list[dict]) -> None:
    """Write the per-configuration score table that prepare-eval-summary.py reads."""
    for cfg in configs:
        root = runs / cfg["config"]
        table = {}
        for bug_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            rows = []
            for run_dir in _run_dirs(bug_dir):
                scores = json.loads((run_dir / "diagnosis_scores.json").read_text())["scores"]
                stats = json.loads((run_dir / "session_stats.json").read_text())
                rows.append({"bug": bug_dir.name, "run": run_dir.name, **scores,
                             **{key: stats.get(key) for key in EFFORT}})
            table[bug_dir.name] = {"runs": rows}
        (root / "aggregated_evaluations.json").write_text(json.dumps(table, indent=2))


def run(script: str, *args: object) -> None:
    subprocess.run([sys.executable, str(PAPER / script), *map(str, args)], check=True)


def differences(ours, ref, path=()) -> list[str]:
    if any(len(path) == len(rule) and all(r in ("*", p) for r, p in zip(rule, path)) for rule in IGNORED):
        return []
    if isinstance(ref, dict) and isinstance(ours, dict):
        keys = set(ref) | set(ours)
        return [d for key in sorted(keys, key=str)
                for d in differences(ours.get(key), ref.get(key), path + (key,))]
    if isinstance(ref, list) and isinstance(ours, list) and len(ref) == len(ours):
        return [d for i, (a, b) in enumerate(zip(ours, ref))
                for d in differences(a, b, path + ("*" if path and path[-1] == "experiments" else i,))]
    if isinstance(ref, (int, float)) and isinstance(ours, (int, float)) and not isinstance(ref, bool):
        return [] if math.isclose(ours, ref, rel_tol=1e-9, abs_tol=1e-12) else ["/".join(map(str, path))]
    return [] if ours == ref else ["/".join(map(str, path))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("traces", type=Path, help="Local copy of the traces dataset")
    parser.add_argument("--out", type=Path, default=PAPER / "out")
    parser.add_argument("--use-released-scores", action="store_true",
                        help="Aggregate the released diagnosis_scores.json instead of rescoring")
    args = parser.parse_args()

    configs = json.loads((args.traces / "configs.json").read_text())
    runs = args.out / "runs"
    unpack(args.traces, runs, configs)
    if not args.use_released_scores:
        rescore(runs, configs)
    write_aggregates(runs, configs)

    summary = args.out / "eval_summary.json"
    reference = PAPER / "reference" / "eval_summary.json"
    run("prepare-eval-summary.py", "--experiments", *[runs / c["config"] for c in configs],
        "--cohort-from", reference, "--first-three-valid", "-o", summary)
    run("render-eval-table.py", summary, "-o", args.out / "eval_main_results.tex")
    run("plot-bench-characterization.py", "-o", args.out / "bench_characterization.pdf")
    run("plot-eval-stratified.py", summary, "-o", args.out / "eval_stratified_block_coverage.pdf")

    diffs = differences(json.loads(summary.read_text()), json.loads(reference.read_text()))
    table_same = ((args.out / "eval_main_results.tex").read_text()
                  == (PAPER / "reference" / "eval_main_results.tex").read_text())
    ids = [c["id"] for c in json.loads(reference.read_text())["experiments"]]
    bad = sorted({i for d in diffs for i in ids if f"/{i}/" in f"/{d}/"})
    print(f"Scores, effort, and CIs match the paper for {len(ids) - len(bad)}/{len(ids)} configurations.")
    print(f"Table 1 {'matches' if table_same else 'DIFFERS from'} paper/reference/eval_main_results.tex.")
    for d in diffs[:20]:
        print("  differs:", d)
    return 0 if not diffs and table_same else 1


if __name__ == "__main__":
    raise SystemExit(main())

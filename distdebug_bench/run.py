"""Run Mini-SWE-Agent over the benchmark, as in the paper.

Each bug is attempted until it has ``--runs`` successful attempts (3 in the paper)
or ``--max-attempts`` attempts in total. Attempts that already have a diagnosis
are kept, so an interrupted sweep resumes where it stopped.

Usage:
    pip install -e ".[agent]"
    python -m distdebug_bench.run --config configs/mini-swe-agent/gpt-5.4.yaml --subset lite
    python -m distdebug_bench.score results/mini-swe-agent-gpt-5.4
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from distdebug_bench.agent import DIAGNOSIS_NAME, AgentConfig, run_attempt
from distdebug_bench.benchmark import load_benchmark
from distdebug_bench.models import BugReport
from distdebug_bench.repos import ensure_system_repo, temporary_checkout


def select_bugs(bench, subset: str, bug_ids: list[str] | None, systems: list[str] | None) -> list[BugReport]:
    bugs = [bench.get_bug(b) for b in bug_ids] if bug_ids else list(bench.iter_bugs(subset))
    if systems:
        bugs = [b for b in bugs if b.system in systems]
    return bugs


@contextmanager
def _redirect_output(path: Path):
    """Send this process's stdout/stderr (including the agent's console) to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout.flush()
    sys.stderr.flush()
    saved = os.dup(1), os.dup(2)
    with path.open("a") as log:
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        try:
            yield
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])


def run_bug(
    bug: BugReport,
    system_repo: Path,
    config: AgentConfig,
    output: Path,
    workspaces: Path,
    runs: int,
    max_attempts: int,
) -> dict:
    """Attempt ``bug`` until it has ``runs`` successful attempts or hits ``max_attempts``."""
    successes, attempts = 0, []
    for index in range(1, max_attempts + 1):
        if successes >= runs:
            break
        run_dir = output / bug.name / f"run-{index:02d}"
        if (run_dir / DIAGNOSIS_NAME).exists() and (run_dir / "trajectory.json").exists():
            successes += 1
            continue
        with temporary_checkout(bug, system_repo, workspaces) as workspace:
            result = run_attempt(bug, workspace, run_dir, config)
        result["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        attempts.append(result)
        with (output / "runs.jsonl").open("a") as log:
            log.write(json.dumps(result) + "\n")
        successes += result["success"]
    return {"bug": bug.name, "successes": successes, "target": runs, "new_attempts": len(attempts)}


def _run_bug_logged(log_path: Path, *args) -> dict:
    with _redirect_output(log_path):
        return run_bug(*args)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", type=Path, required=True, help="Model config, e.g. configs/mini-swe-agent/gpt-5.4.yaml")
    parser.add_argument("--benchmark", type=Path, default=Path("."), help="Repository root containing benchmark/")
    parser.add_argument("--subset", choices=("all", "lite"), default="all")
    parser.add_argument("--bug", dest="bugs", action="append", help="Run only these bugs (repeatable)")
    parser.add_argument("--system", dest="systems", action="append", help="Run only these systems (repeatable)")
    parser.add_argument("--runs", type=int, default=3, help="Successful attempts per bug (default: 3)")
    parser.add_argument("--max-attempts", type=int, default=30, help="Attempts per bug before giving up (default: 30)")
    parser.add_argument("--parallel", type=int, default=1, help="Bugs to run concurrently (default: 1)")
    parser.add_argument("--output", type=Path, help="Results directory (default: results/<config name>)")
    parser.add_argument("--cache", type=Path, default=Path(".cache/repos"), help="Where to clone the systems")
    parser.add_argument("--workspaces", type=Path, default=Path(".cache/workspaces"), help="Where to create workspaces")
    args = parser.parse_args(argv)

    config = AgentConfig.from_yaml(args.config)
    bench = load_benchmark(args.benchmark)
    bugs = select_bugs(bench, args.subset, args.bugs, args.systems)
    output = (args.output or Path("results") / config.name).resolve()
    output.mkdir(parents=True, exist_ok=True)
    workspaces = args.workspaces.resolve()
    # Clone each system once, before any workers start.
    repos = {s: ensure_system_repo(bench.systems[s], args.cache).resolve() for s in sorted({b.system for b in bugs})}
    print(f"[run] {config.name}: {len(bugs)} bugs, {args.runs} successful attempts each -> {output}")

    jobs = [(bug, repos[bug.system], config, output, workspaces, args.runs, args.max_attempts) for bug in bugs]
    if args.parallel <= 1:
        summaries = [run_bug(*job) for job in jobs]
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.parallel) as pool:
            futures = [pool.submit(_run_bug_logged, output / job[0].name / "worker.log", *job) for job in jobs]
            summaries = []
            for future in concurrent.futures.as_completed(futures):
                summaries.append(summary := future.result())
                print(f"[run] {summary['bug']}: {summary['successes']}/{summary['target']}")

    incomplete = [s["bug"] for s in summaries if s["successes"] < s["target"]]
    print(f"[run] done: {len(summaries) - len(incomplete)}/{len(summaries)} bugs complete")
    if incomplete:
        print(f"[run] incomplete: {', '.join(sorted(incomplete))}")
    return 0 if not incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())

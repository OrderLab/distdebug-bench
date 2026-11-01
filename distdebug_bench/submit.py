"""Package and check a leaderboard submission.

``package`` scores a results directory, runs the leakage guard, and writes:

    <out>/<YYYYMMDD>_<agent>__<model>/
        metadata.yaml
        summary.json                  # from distdebug_bench.score
        leakage.json                  # from distdebug_bench.leakage
        diagnoses/<bug>/run-NN/{diagnosis.json,session_stats.json}
    <out>/<YYYYMMDD>_<agent>__<model>-trajectories.tar.gz

Upload the archive anywhere public, put its link in ``trajectories_url``, run
``check``, and open a pull request adding the submission directory.

Usage:
    python -m distdebug_bench.submit package results/my-run --metadata metadata.yaml --out submissions
    python -m distdebug_bench.submit check submissions/20260101_my-agent__my-model
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
from pathlib import Path

from distdebug_bench.benchmark import load_benchmark
from distdebug_bench.leakage import scan_trajectory
from distdebug_bench.score import METRICS, _run_dirs, summarize

ATTEMPTS = 3
REQUIRED = (
    "agent_name", "agent_url", "open_source", "models", "model_settings", "subset",
    "date", "organization", "report_url", "trajectories_url", "contact",
)


def _load_yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text()) or {}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", str(text).lower()).strip("-")


def check_metadata(meta: dict, allow_missing_trajectories: bool = False) -> list[str]:
    errors = []
    for key in REQUIRED:
        value = meta.get(key)
        placeholder = isinstance(value, str) and (value.strip() in ("", "https://...") or "example.com" in value)
        if value in (None, "", []) or placeholder:
            if not (key == "trajectories_url" and allow_missing_trajectories):
                errors.append(f"metadata: fill in `{key}`")
    if meta.get("subset") not in ("all", "lite"):
        errors.append("metadata: `subset` must be `all` or `lite`")
    if not isinstance(meta.get("models"), list):
        errors.append("metadata: `models` must be a list")
    return errors


def check_completeness(summary: dict) -> list[str]:
    errors = []
    if summary["incomplete_bugs"]:
        missing = ", ".join(f"{b} ({n})" for b, n in sorted(summary["incomplete_bugs"].items())[:10])
        errors.append(
            f"{len(summary['incomplete_bugs'])} bugs have fewer than {ATTEMPTS} valid attempts, e.g. {missing}"
        )
    return errors


def package(results: Path, metadata_path: Path, out: Path, benchmark: Path) -> int:
    meta = _load_yaml(metadata_path)
    errors = check_metadata(meta, allow_missing_trajectories=True)
    if errors:
        print("\n".join(errors))
        return 1
    name = f"{str(meta['date']).replace('-', '')}_{_slug(meta['agent_name'])}__{_slug(meta['models'][0])}"
    target = out / name
    if target.exists():
        print(f"{target} already exists")
        return 1

    summary = summarize(results, benchmark, meta["subset"], ATTEMPTS)
    bench = load_benchmark(benchmark)
    flagged = {}
    for bug in bench.iter_bugs(meta["subset"]):
        for run_dir in _run_dirs(results / bug.name):
            path = run_dir / "trajectory.json"
            if path.exists() and (matches := scan_trajectory(path, bug.name)):
                flagged[f"{bug.name}/{run_dir.name}"] = matches
    errors = check_completeness(summary)
    if flagged:
        errors.append(
            f"{len(flagged)} attempts name the original issue; run `python -m distdebug_bench.leakage "
            f"{results} --quarantine`, rerun them, and package again"
        )
    if errors:
        print("\n".join(errors))
        return 1

    target.mkdir(parents=True)
    shutil.copy(metadata_path, target / "metadata.yaml")
    (target / "summary.json").write_text(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    (target / "leakage.json").write_text(json.dumps({"flagged": 0}, indent=2))
    archive = out / f"{name}-trajectories.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for bug in bench.iter_bugs(meta["subset"]):
            for run_dir in _run_dirs(results / bug.name):
                for file in ("diagnosis.json", "session_stats.json"):
                    if (run_dir / file).exists():
                        dest = target / "diagnoses" / bug.name / run_dir.name / file
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy(run_dir / file, dest)
                if (run_dir / "trajectory.json").exists():
                    tar.add(run_dir / "trajectory.json", arcname=f"{name}/{bug.name}/{run_dir.name}/trajectory.json")

    print(f"Packaged {target}")
    print(f"  block coverage {summary['metrics']['block_coverage']:.1%} on {summary['bugs_scored']} bugs")
    print(f"Next: upload {archive} somewhere public, set `trajectories_url` in {target / 'metadata.yaml'},")
    print(f"      run `python -m distdebug_bench.submit check {target}`, and open a pull request.")
    return 0


def check(submission: Path, benchmark: Path) -> int:
    meta = _load_yaml(submission / "metadata.yaml")
    errors = check_metadata(meta)
    if not errors:
        rescored = summarize(submission / "diagnoses", benchmark, meta["subset"], ATTEMPTS, write=False)
        errors += check_completeness(rescored)
        reported = json.loads((submission / "summary.json").read_text())["metrics"]
        for key in METRICS:
            if abs(rescored["metrics"].get(key, 0) - reported.get(key, 0)) > 1e-9:
                errors.append(f"summary.json `{key}` does not match the diagnoses")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"{submission.name}: OK, block coverage {rescored['metrics']['block_coverage']:.1%}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("package", help="Build a submission directory and trajectories archive")
    p.add_argument("results", type=Path, help="Results directory written by distdebug_bench.run")
    p.add_argument("--metadata", type=Path, required=True, help="Filled-in metadata.yaml")
    p.add_argument("--out", type=Path, default=Path("submissions"))
    c = sub.add_parser("check", help="Validate a packaged submission directory")
    c.add_argument("submission", type=Path)
    for s in (p, c):
        s.add_argument("--benchmark", type=Path, default=Path("."), help="Repository root containing benchmark/")
    args = parser.parse_args(argv)
    if args.command == "package":
        return package(args.results.resolve(), args.metadata, args.out, args.benchmark)
    return check(args.submission, args.benchmark)


if __name__ == "__main__":
    sys.exit(main())

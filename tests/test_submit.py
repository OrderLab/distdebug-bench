"""Tests for packaging and checking leaderboard submissions."""

from __future__ import annotations

import json
import shutil
import tarfile
from pathlib import Path

import pytest

from distdebug_bench import load_benchmark
from distdebug_bench.submit import check, package

yaml = pytest.importorskip("yaml")
REPO_ROOT = Path(__file__).resolve().parents[1]


def _results(tmp_path: Path, leak: bool = False) -> Path:
    """Three gold-matching attempts for every Lite bug."""
    results = tmp_path / "results"
    for bug in load_benchmark(REPO_ROOT).iter_bugs("lite"):
        blocks = [
            {**b.to_dict(), "bug_contribution": "", "patch": ""} for b in bug.ground_truth_blocks
        ]
        for run in (1, 2, 3):
            run_dir = results / bug.name / f"run-{run:02d}"
            run_dir.mkdir(parents=True)
            (run_dir / "diagnosis.json").write_text(
                json.dumps({"buggy_blocks": blocks, "bug_cause_description": "cause"})
            )
            text = f"git log mentions {bug.system.upper()}-{bug.bug_id}" if leak and run == 1 else "reading code"
            (run_dir / "trajectory.json").write_text(json.dumps({"messages": [{"role": "assistant", "content": text}]}))
    return results


def _metadata(tmp_path: Path, **overrides) -> Path:
    meta = yaml.safe_load((REPO_ROOT / "submissions/metadata.template.yaml").read_text())
    meta.update({
        "subset": "lite", "organization": "Lab", "report_url": "https://example.org/report",
        "trajectories_url": "https://example.org/traj.tar.gz", "contact": "A <a@lab.org>", **overrides,
    })
    path = tmp_path / "metadata.yaml"
    path.write_text(yaml.safe_dump(meta))
    return path


def test_package_and_check(tmp_path):
    out = tmp_path / "submissions"
    assert package(_results(tmp_path), _metadata(tmp_path), out, REPO_ROOT) == 0
    [submission] = [p for p in out.iterdir() if p.is_dir()]
    assert submission.name == "20260101_mini-swe-agent__gpt-5.4"
    summary = json.loads((submission / "summary.json").read_text())
    assert summary["metrics"]["block_coverage"] == 1.0 and summary["bugs_scored"] == 34
    assert not list(submission.rglob("trajectory.json"))
    with tarfile.open(out / f"{submission.name}-trajectories.tar.gz") as tar:
        assert len(tar.getnames()) == 34 * 3
    assert check(submission, REPO_ROOT) == 0

    summary["metrics"]["block_coverage"] = 0.99
    (submission / "summary.json").write_text(json.dumps(summary))
    assert check(submission, REPO_ROOT) == 1


def test_check_rejects_incomplete_or_placeholder_submissions(tmp_path):
    out = tmp_path / "submissions"
    results = _results(tmp_path)
    shutil.rmtree(results / "zookeeper-2845" / "run-03")
    assert package(results, _metadata(tmp_path), out, REPO_ROOT) == 1  # a bug has 2 attempts
    assert package(_results(tmp_path / "b"), _metadata(tmp_path, contact="You <you@example.com>"), out, REPO_ROOT) == 1


def test_package_rejects_leaked_attempts(tmp_path):
    assert package(_results(tmp_path, leak=True), _metadata(tmp_path), tmp_path / "out", REPO_ROOT) == 1

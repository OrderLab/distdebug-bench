"""Smoke tests for the distdebug_bench loader and evaluator."""

from __future__ import annotations

from pathlib import Path

import pytest

from distdebug_bench import compute_correctness_report, get_bug, load_benchmark
from distdebug_bench.models import BuggyBlock

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_load_benchmark_basic():
    bench = load_benchmark(REPO_ROOT)
    assert "zookeeper" in bench.systems
    assert bench.systems["zookeeper"].url == "https://github.com/apache/zookeeper"
    assert bench.systems["zookeeper"].language == "java"
    assert len(bench.lite_bug_ids) == 34
    assert set(bench.lite_bug_ids) <= {bug.name for bug in bench.iter_bugs("all")}


def test_load_benchmark_accepts_benchmark_subdir():
    """Both repo root and the inner benchmark/ directory should resolve."""
    a = load_benchmark(REPO_ROOT)
    b = load_benchmark(REPO_ROOT / "benchmark")
    assert a.benchmark_dir == b.benchmark_dir


def test_zk_2845_ground_truth():
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    assert bug.system == "zookeeper"
    assert bug.bug_id == "2845"
    assert bug.branch == "release-3.5.3"
    assert "Data inconsistency" in bug.title
    assert len(bug.ground_truth_blocks) == 1
    block = bug.ground_truth_blocks[0]
    assert block.file.endswith("ZooKeeperServer.java")
    assert block.function == "ZooKeeperServer.shutdown"
    assert block.start_line == 555
    assert block.end_line == 558


def test_all_bugs_load():
    """All 120 bugs must load with non-empty ground truth and a known system."""
    bench = load_benchmark(REPO_ROOT)
    bugs = list(bench.iter_bugs("all"))
    assert len(bugs) == 120
    for bug in bugs:
        assert bug.ground_truth_blocks, f"{bug.name} has no ground-truth blocks"
        assert bug.system in bench.systems, f"{bug.name} references unknown system {bug.system!r}"
        assert bug.branch, f"{bug.name} has empty branch"


def test_string_line_numbers_are_coerced():
    """elasticsearch-105065's blocks.json uses string line numbers; loader must coerce to int."""
    bug = get_bug(REPO_ROOT, "elasticsearch-105065")
    for block in bug.ground_truth_blocks:
        assert isinstance(block.start_line, int)
        assert isinstance(block.end_line, int)


def test_eval_perfect_match():
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    report = compute_correctness_report(bug.ground_truth_blocks, bug.ground_truth_blocks)
    for granularity in ("file", "function", "line", "block"):
        assert report[f"{granularity}_precision"] == 1.0
        assert report[f"{granularity}_coverage"] == 1.0


def test_eval_total_miss():
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    wrong = [BuggyBlock(file="nope.java", function="X.y", start_line=1, end_line=2)]
    report = compute_correctness_report(bug.ground_truth_blocks, wrong)
    for granularity in ("file", "function", "line", "block"):
        assert report[f"{granularity}_precision"] == 0.0
        assert report[f"{granularity}_coverage"] == 0.0


def test_eval_partial_file_only():
    """Predicting the right file but wrong function/lines yields file=1.0 but block=0.0."""
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    gt = bug.ground_truth_blocks[0]
    pred = [BuggyBlock(file=gt.file, function="WrongClass.wrongMethod", start_line=1, end_line=2)]
    report = compute_correctness_report(bug.ground_truth_blocks, pred)
    assert report["file_precision"] == 1.0
    assert report["file_coverage"] == 1.0
    assert report["function_precision"] == 0.0
    assert report["block_precision"] == 0.0


def test_eval_empty_ground_truth_scores_zero():
    pred = [BuggyBlock(file="a", function="b", start_line=1, end_line=2)]
    report = compute_correctness_report([], pred)
    assert report["file_coverage"] == 0.0
    assert report["file_precision"] == 0.0  # nothing in GT to match against


def _zk2845_prediction(**overrides) -> list[BuggyBlock]:
    """The paper's scoring rules, exercised on ZooKeeper-2845 (gold: lines 555-558)."""
    block = dict(
        file="src/java/main/org/apache/zookeeper/server/ZooKeeperServer.java",
        function="ZooKeeperServer.shutdown",
        start_line=555,
        end_line=558,
    )
    block.update(overrides)
    return [BuggyBlock(**block)]


def test_eval_canonicalizes_function_names():
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    pred = _zk2845_prediction(
        function="org.apache.zookeeper.server.ZooKeeperServer.shutdown(boolean)",
        start_line=528,
        end_line=562,
    )
    report = compute_correctness_report(bug.ground_truth_blocks, pred)
    assert report["function_coverage"] == 1.0
    assert report["block_coverage"] == 1.0


def test_eval_matches_contained_paths():
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    pred = _zk2845_prediction(
        file="/workspace/zookeeper/src/java/main/org/apache/zookeeper/server/ZooKeeperServer.java"
    )
    report = compute_correctness_report(bug.ground_truth_blocks, pred)
    assert report["file_coverage"] == 1.0
    assert report["block_coverage"] == 1.0


def test_eval_block_tolerance_and_half_open_ranges():
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    # Within the 5-line tolerance but not overlapping: block hit, no strict or line hit.
    near = compute_correctness_report(bug.ground_truth_blocks, _zk2845_prediction(start_line=560, end_line=570))
    assert near["block_coverage"] == 1.0
    assert near["strict_block_coverage"] == 0.0
    assert near["line_coverage"] == 0.0
    # Ranges are [start, end): a prediction starting at the gold end_line does not overlap.
    touching = compute_correctness_report(bug.ground_truth_blocks, _zk2845_prediction(start_line=558, end_line=600))
    assert touching["strict_block_coverage"] == 0.0
    assert touching["block_coverage"] == 1.0
    # Beyond the tolerance: no block hit.
    far = compute_correctness_report(bug.ground_truth_blocks, _zk2845_prediction(start_line=570, end_line=580))
    assert far["block_coverage"] == 0.0


def test_eval_mention_coverage():
    specific = [BuggyBlock(file="a.java", function="ZooKeeperServer.shutdown", start_line=1, end_line=2)]
    assert compute_correctness_report(specific, [], "calls shutdown() too early")["mention_coverage"] == 1.0
    generic = [BuggyBlock(file="a.java", function="WorkerSourceTask.run", start_line=1, end_line=2)]
    assert compute_correctness_report(generic, [], "the run loop")["mention_coverage"] == 0.0
    assert compute_correctness_report(generic, [], "WorkerSourceTask.run() exits")["mention_coverage"] == 1.0


def test_eval_empty_prediction():
    bug = get_bug(REPO_ROOT, "zookeeper-2845")
    report = compute_correctness_report(bug.ground_truth_blocks, [])
    assert report["file_precision"] == 0.0
    assert report["file_coverage"] == 0.0

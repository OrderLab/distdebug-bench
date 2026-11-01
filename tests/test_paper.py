"""The paper's reference results are consistent with the scripts in paper/."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

PAPER = Path(__file__).resolve().parents[1] / "paper"
REFERENCE = PAPER / "reference" / "eval_summary.json"
sys.path.insert(0, str(PAPER))

from eval_confidence import bootstrap_mean  # noqa: E402


@pytest.fixture(scope="module")
def reference():
    return json.loads(REFERENCE.read_text())


def test_table_1_renders_from_reference_summary(tmp_path):
    out = tmp_path / "eval_main_results.tex"
    subprocess.run([sys.executable, str(PAPER / "render-eval-table.py"), str(REFERENCE), "-o", str(out)],
                   check=True, capture_output=True)
    assert out.read_text() == (PAPER / "reference" / "eval_main_results.tex").read_text()


def test_block_coverage_intervals_follow_from_attempt_scores(reference):
    bugs = sorted(reference["metadata"]["common_bug_ids"])
    assert len(bugs) == 120
    for exp in reference["experiments"]:
        attempts = reference["per_bug_attempt_scores"][exp["id"]]
        assert all(len(attempts[bug]["block_coverage"]) == 3 for bug in bugs)
        ci = bootstrap_mean([float(np.mean(attempts[bug]["block_coverage"])) for bug in bugs])
        recorded = reference["confidence_intervals"]["block_coverage"][exp["id"]]
        assert ci["mean"] == pytest.approx(reference["metrics"]["block_coverage"][exp["id"]])
        assert (ci["low"], ci["high"]) == pytest.approx((recorded["low"], recorded["high"]))


def test_labels_cover_every_reference_configuration(reference):
    spec = importlib.util.spec_from_file_location("prepare_eval_summary", PAPER / "prepare-eval-summary.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert list(module.EXPERIMENT_LABELS) == [exp["name"] for exp in reference["experiments"]]

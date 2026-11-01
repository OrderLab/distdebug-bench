"""Tests for the agent runner, workspaces, scoring, and leakage guard."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from distdebug_bench import get_bug
from distdebug_bench.agent import ARTIFACTS_DIR, VALIDATOR_NAME, AgentConfig, build_task, stage_workspace
from distdebug_bench.leakage import scan_trajectory
from distdebug_bench.models import BugReport, BuggyBlock
from distdebug_bench.repos import temporary_checkout
from distdebug_bench.score import summarize

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _make_system_repo(path: Path) -> tuple[str, str]:
    """A repo with a pre-fix commit and a later fix commit. Returns (pre_fix, fix)."""
    path.mkdir()
    _git("init", "--quiet", "-b", "main", cwd=path)
    _git("config", "user.email", "t@example.com", cwd=path)
    _git("config", "user.name", "t", cwd=path)
    (path / "Server.java").write_text("class Server {\n  void shutdown() {\n    db.clear();\n  }\n}\n")
    _git("add", ".", cwd=path)
    _git("commit", "--quiet", "-m", "initial", cwd=path)
    pre_fix = _git("rev-parse", "HEAD", cwd=path)
    (path / "Server.java").write_text("class Server {\n  void shutdown() {\n  }\n}\n")
    _git("commit", "--quiet", "-am", "TOY-1: fix shutdown", cwd=path)
    _git("tag", "v2", cwd=path)
    return pre_fix, _git("rev-parse", "HEAD", cwd=path)


def _toy_bug(pre_fix: str) -> BugReport:
    return BugReport(
        name="toy-1", system="toy", bug_id="1", branch=pre_fix, title="Data lost on shutdown",
        description="Restarting the server loses data.", severity="", root_cause_type="",
        bug_complexity="", distributed="", issue_link="", fix_link="", artifact_dir=None,
        ground_truth_blocks=[BuggyBlock(file="Server.java", function="Server.shutdown", start_line=2, end_line=4)],
        raw_report={},
    )


def test_workspace_hides_later_commits(tmp_path):
    pre_fix, fix = _make_system_repo(tmp_path / "system")
    with temporary_checkout(_toy_bug(pre_fix), tmp_path / "system", tmp_path / "ws") as workspace:
        assert workspace.name.startswith("workspace-")
        assert _git("rev-parse", "HEAD", cwd=workspace) == pre_fix
        assert _git("for-each-ref", cwd=workspace) == ""
        assert "TOY-1" not in _git("log", "--all", "--format=%s", cwd=workspace)
        missing = subprocess.run(["git", "cat-file", "-e", fix], cwd=workspace, capture_output=True)
        assert missing.returncode != 0, "the fix commit must not be reachable"
    assert not workspace.exists()


def test_workspace_fetches_snapshots_missing_from_the_clone(tmp_path):
    """Snapshots on deleted upstream branches are fetched by SHA."""
    upstream = tmp_path / "upstream"
    _make_system_repo(upstream)
    _git("config", "uploadpack.allowAnySHA1InWant", "true", cwd=upstream)
    _git("clone", "--quiet", str(upstream), str(tmp_path / "cache"), cwd=tmp_path)
    _git("checkout", "--quiet", "-b", "feature", cwd=upstream)
    (upstream / "Extra.java").write_text("class Extra {}\n")
    _git("add", ".", cwd=upstream)
    _git("commit", "--quiet", "-m", "feature work", cwd=upstream)
    orphaned = _git("rev-parse", "HEAD", cwd=upstream)
    _git("checkout", "--quiet", "main", cwd=upstream)
    _git("branch", "--quiet", "-D", "feature", cwd=upstream)
    with temporary_checkout(_toy_bug(orphaned), tmp_path / "cache", tmp_path / "ws") as workspace:
        assert _git("rev-parse", "HEAD", cwd=workspace) == orphaned


def test_task_lists_staged_artifacts(tmp_path):
    bug = get_bug(REPO_ROOT, "zookeeper-3769")
    names = stage_workspace(bug, tmp_path)
    assert names == sorted(p.name for p in bug.artifact_dir.iterdir())
    assert (tmp_path / VALIDATOR_NAME).is_file()
    assert all((tmp_path / ARTIFACTS_DIR / n).is_file() for n in names)
    task = build_task(bug, names)
    assert task.startswith(f"Title: {bug.title}")
    assert f"./{ARTIFACTS_DIR}/" in task and all(f"- {n}" in task for n in names)
    assert build_task(bug, []) == bug.task_prompt


def test_paper_configs_load():
    pytest.importorskip("yaml")
    configs = [AgentConfig.from_yaml(p) for p in sorted((REPO_ROOT / "configs/mini-swe-agent").glob("*.yaml"))]
    assert len(configs) == 13
    assert len({c.name for c in configs}) == 13
    assert all(c.cost_limit == 3.0 for c in configs)
    opus = next(c for c in configs if "opus" in c.name)
    assert Path(opus.env["LITELLM_MODEL_REGISTRY_PATH"]).is_file()


def _write_run(results: Path, bug: str, run: int, diagnosis: dict) -> None:
    run_dir = results / bug / f"run-{run:02d}"
    run_dir.mkdir(parents=True)
    (run_dir / "diagnosis.json").write_text(json.dumps(diagnosis))


def _diagnosis(file: str, start: int, end: int) -> dict:
    block = {"file": file, "function": "ZooKeeperServer.shutdown", "start_line": start, "end_line": end,
             "bug_contribution": "", "patch": ""}
    return {"buggy_blocks": [block], "bug_cause_description": "shutdown keeps zkDb"}


def test_score_selects_first_valid_attempts(tmp_path):
    gold = get_bug(REPO_ROOT, "zookeeper-2845").ground_truth_blocks[0]
    _write_run(tmp_path, "zookeeper-2845", 1, {"buggy_blocks": []})  # schema-invalid: skipped
    _write_run(tmp_path, "zookeeper-2845", 2, _diagnosis(gold.file, gold.start_line, gold.end_line))
    _write_run(tmp_path, "zookeeper-2845", 3, _diagnosis("Other.java", 1, 2))  # valid, zero score
    _write_run(tmp_path, "zookeeper-2845", 4, _diagnosis(gold.file, gold.start_line, gold.end_line))
    _write_run(tmp_path, "zookeeper-2845", 5, _diagnosis(gold.file, gold.start_line, gold.end_line))
    summary = summarize(tmp_path, REPO_ROOT, "lite", attempts=3)
    assert summary["per_bug"]["zookeeper-2845"]["block_coverage"] == pytest.approx(2 / 3)
    assert summary["metrics"]["block_coverage"] == pytest.approx(2 / 3)
    scored = sorted(p.parent.name for p in tmp_path.glob("zookeeper-2845/run-*/diagnosis_scores.json"))
    assert scored == ["run-02", "run-03", "run-04"]
    assert summary["bugs_scored"] == 1 and "zookeeper-2845" not in summary["incomplete_bugs"]


def test_leakage_flags_issue_ids_in_model_output(tmp_path):
    def scan(messages):
        path = tmp_path / "trajectory.json"
        path.write_text(json.dumps({"messages": messages}))
        return scan_trajectory(path, "kafka-13407")

    prompt = {"role": "system", "content": "Diagnose KAFKA-13407."}
    task = {"role": "user", "content": "Title: Consumer hangs"}
    assert scan([prompt, task]) == []  # the prompt is not scanned
    leaked = {"role": "assistant", "content": "git log shows KAFKA-13407: fix the race"}
    assert [m["match"] for m in scan([prompt, task, leaked])] == ["KAFKA-13407"]
    path_only = {"role": "user", "content": "<returncode>0</returncode>\n<output>/home/u/results/kafka-13407/x</output>"}
    assert scan([prompt, task, path_only]) == []


def _bwrap_works() -> bool:
    return shutil.which("bwrap") is not None and subprocess.run(
        ["bwrap", "--unshare-user-try", "--ro-bind", "/", "/", "true"], capture_output=True
    ).returncode == 0


@pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is unavailable")
def test_end_to_end_with_scripted_model(tmp_path):
    """Run the real agent loop with a scripted model: no API calls."""
    pytest.importorskip("minisweagent")
    from minisweagent.models.test_models import make_output

    from distdebug_bench.agent import run_attempt
    from distdebug_bench.score import score_run

    pre_fix, _ = _make_system_repo(tmp_path / "system")
    bug = _toy_bug(pre_fix)
    diagnosis = {"buggy_blocks": [{"file": "Server.java", "function": "Server.shutdown", "start_line": 2,
                                   "end_line": 4, "bug_contribution": "clears the db", "patch": ""}],
                 "bug_cause_description": "shutdown clears the database"}
    write = f"cat > diagnosis.json <<'EOF'\n{json.dumps(diagnosis)}\nEOF\npython3 {VALIDATOR_NAME} diagnosis.json"
    config = AgentConfig(
        name="scripted",
        model_name="deterministic",
        model_class="minisweagent.models.test_models.DeterministicModel",
        cost_limit=100.0,
        mini_config={"model.outputs": [
            make_output("Write the diagnosis.", [{"command": write}]),
            make_output("Submit.", [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}]),
        ]},
    )
    run_dir = tmp_path / "results" / "toy-1" / "run-01"
    with temporary_checkout(bug, tmp_path / "system", tmp_path / "ws") as workspace:
        result = run_attempt(bug, workspace, run_dir, config)
    assert result["success"], result["failed_reason"]
    assert json.loads((run_dir / "diagnosis.json").read_text()) == diagnosis
    assert json.loads((run_dir / "session_stats.json").read_text())["api_calls"] == 2
    info = json.loads((run_dir / "trajectory.json").read_text())["info"]
    assert info["mini_version"] == "2.2.8"
    assert info["config"]["environment_type"] == "distdebug_bench.sandbox.SandboxEnvironment"
    assert info["config"]["environment"]["blocked_commands"] == ["chafa", "img2txt"]
    assert score_run(bug, run_dir)["block_coverage"] == 1.0


@pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is unavailable")
def test_sandbox_blocks_commands(tmp_path):
    pytest.importorskip("minisweagent")
    from distdebug_bench.sandbox import SandboxEnvironment

    env = SandboxEnvironment(cwd=str(tmp_path), timeout=10, blocked_commands=["chafa", "img2txt"])
    out = env.execute({"command": "chafa x.png; echo rc=$?; echo ok > f.txt; cat f.txt"})
    assert "chafa is disabled in this evaluation sandbox." in out["output"]
    assert "rc=127" in out["output"] and "ok" in out["output"]
    assert (tmp_path / "f.txt").read_text() == "ok\n"


@pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap is unavailable")
def test_sandbox_has_no_network(tmp_path):
    pytest.importorskip("minisweagent")
    from distdebug_bench.sandbox import SandboxEnvironment

    probe = "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), timeout=3)\"; echo rc=$?"
    out = SandboxEnvironment(cwd=str(tmp_path), timeout=20).execute({"command": probe})
    assert "rc=0" not in out["output"]
    assert "unreachable" in out["output"].lower()

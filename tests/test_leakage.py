"""Leakage guard on every supported trace format."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from distdebug_bench import leakage
from distdebug_bench.leakage import bug_identifiers, scan_trajectory

ROOT = Path(__file__).resolve().parents[1]

# KAFKA-13407 was fixed by apache/kafka#11563.
BUG = SimpleNamespace(
    issue_link="https://issues.apache.org/jira/browse/KAFKA-13407",
    fix_link="https://github.com/apache/kafka/pull/11563/files",
    task_prompt="Title: Consumer hangs after a rebalance\n\nThe consumer stops fetching.",
)
SYSTEM = {"role": "system", "content": "You are debugging a distributed system."}
TASK = {"role": "user", "content": BUG.task_prompt}
CURL_FIX = "curl -s https://api.github.com/repos/apache/kafka/pulls/11563 | head -50"
# A source listing whose line numbers include both numbers: not leakage.
LISTING = "\n".join(f"{n}\t    return records;" for n in (11562, 11563, 13406, 13407, 13408))


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def _trajectory(tmp_path: Path, *messages) -> Path:
    return _write(tmp_path, "trajectory.json", json.dumps({"messages": [SYSTEM, TASK, *messages]}))


def _jsonl(tmp_path: Path, name: str, events) -> Path:
    return _write(tmp_path, name, "\n".join(json.dumps(e) for e in events) + "\n")


def _rules(path: Path) -> list[tuple[str, str]]:
    return sorted({(m["rule"], m["scope"]) for m in scan_trajectory(path, "kafka-13407", BUG)})


# --- Responses API (function_call / function_call_output) -------------------------------------


def _responses_call(command: str) -> dict:
    return {"object": "response", "output": [
        {"type": "reasoning", "summary": [{"type": "summary_text", "text": "Inspect the consumer."}]},
        {"type": "function_call", "name": "bash", "call_id": "c1", "arguments": json.dumps({"command": command})},
    ]}


def _responses_output(text: str) -> dict:
    return {"type": "function_call_output", "call_id": "c1", "output": json.dumps({"returncode": 0, "output": text})}


def test_responses_fix_pr_fetch_is_flagged(tmp_path):
    path = _trajectory(tmp_path, _responses_call(CURL_FIX), _responses_output("{}"))
    assert _rules(path) == [("reference", "assistant_command")]


def test_responses_line_numbers_are_not_flagged(tmp_path):
    path = _trajectory(tmp_path, _responses_call("nl -ba Fetcher.java | sed -n '11560,13410p'"), _responses_output(LISTING))
    assert _rules(path) == []


def test_responses_issue_key_in_tool_output_is_flagged(tmp_path):
    path = _trajectory(tmp_path, _responses_call("git log -1"), _responses_output("KAFKA-13407: fix the hang (#11563)"))
    assert _rules(path) == [("issue_key", "tool_output"), ("reference", "tool_output")]


# --- Chat Completions tool calls (assistant tool_calls / role: tool) -------------------------


def _chat_call(command: str, content: str = "", **reasoning) -> dict:
    return {"role": "assistant", "content": content, "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": json.dumps({"command": command})}},
    ], **reasoning}


def _chat_output(text: str) -> dict:
    return {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"returncode": 0, "output": text})}


def test_chat_tool_call_with_bare_issue_number_is_flagged(tmp_path):
    path = _trajectory(tmp_path, _chat_call("git log --oneline --grep 13407"), _chat_output(""))
    assert _rules(path) == [("number", "assistant_command")]


def test_chat_fix_pr_fetch_is_flagged(tmp_path):
    path = _trajectory(tmp_path, _chat_call(CURL_FIX), _chat_output('{"title": "Fix consumer hang"}'))
    assert _rules(path) == [("reference", "assistant_command")]


def test_chat_tool_output_reference_is_flagged(tmp_path):
    path = _trajectory(tmp_path, _chat_call("git log -3 --oneline"), _chat_output("abc1234 Fix fetch hang (#11563)"))
    assert _rules(path) == [("reference", "tool_output")]


def test_chat_line_numbers_are_not_flagged(tmp_path):
    path = _trajectory(
        tmp_path,
        _chat_call("sed -n 13400,13410p Fetcher.java", content="Line 13407 resets the position."),
        _chat_output(LISTING),
        _chat_call("head -n 11563 Fetcher.java | tail -n 5"),
        _chat_output(LISTING),
    )
    assert _rules(path) == []


def test_chat_reasoning_fields_are_scanned(tmp_path):
    path = _trajectory(tmp_path, _chat_call("ls", reasoning_content="This looks like KAFKA-13407."), _chat_output(""))
    assert _rules(path) == [("issue_key", "assistant_reasoning")]


def test_chat_encrypted_signatures_are_ignored(tmp_path):
    call = _chat_call("ls")
    call["tool_calls"][0]["extra_content"] = {"google": {"thought_signature": "Eq+13407+11563+"}}
    path = _trajectory(tmp_path, call, _chat_output(""))
    assert _rules(path) == []


# --- Legacy text format (<returncode>/<output> user messages) --------------------------------


def test_legacy_text_format(tmp_path):
    shell = {"role": "assistant", "content": "```bash\ngit log --grep=13407\n```"}
    output = {"role": "user", "content": "<returncode>0</returncode>\n<output>/home/u/results/kafka-13407/x</output>"}
    assert _rules(_trajectory(tmp_path, output)) == []  # path only
    assert _rules(_trajectory(tmp_path, shell, output)) == [("number", "assistant_reasoning")]


# --- Codex CLI (codex exec --json) ----------------------------------------------------------


def _codex(command: str, output: str) -> list[dict]:
    item = {"id": "item_1", "type": "command_execution", "command": f"bash -lc {json.dumps(command)}"}
    return [
        {"type": "thread.started", "thread_id": "t"},
        {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "Look at the fetcher."}},
        {"type": "item.started", "item": {**item, "aggregated_output": "", "status": "in_progress"}},
        {"type": "item.completed", "item": {**item, "aggregated_output": output, "exit_code": 0, "status": "completed"}},
        {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": '{"buggy_blocks": []}'}},
    ]


def test_codex_fix_pr_fetch_is_flagged(tmp_path):
    path = _jsonl(tmp_path, "codex_exec.log", _codex("curl -sL https://github.com/apache/kafka/pull/11563", "<html>"))
    assert _rules(path) == [("reference", "assistant_command")]


def test_codex_line_numbers_are_not_flagged(tmp_path):
    path = _jsonl(tmp_path, "codex_exec.log", _codex("nl -ba Fetcher.java | sed -n '13400,13410p'", LISTING))
    assert _rules(path) == []


def test_codex_web_search_is_flagged(tmp_path):
    events = [{"type": "item.completed", "item": {"id": "item_0", "type": "web_search", "query": "KAFKA-13407 fix"}}]
    assert _rules(_jsonl(tmp_path, "codex_exec.log", events)) == [("issue_key", "assistant_command")]


# --- OpenCode (opencode run --format json) ---------------------------------------------------


def _opencode(tool: str, tool_input: dict, output: str) -> list[dict]:
    return [
        {"type": "step_start", "part": {"type": "step-start"}},
        {"type": "tool_use", "part": {"type": "tool", "tool": tool, "callID": "call_1",
                                      "state": {"status": "completed", "input": tool_input, "output": output}}},
        {"type": "text", "part": {"type": "text", "text": '{"buggy_blocks": []}'}},
    ]


def test_opencode_fix_pr_fetch_is_flagged(tmp_path):
    events = _opencode("bash", {"command": CURL_FIX, "description": "Fetch PR"}, "{}")
    assert _rules(_jsonl(tmp_path, "opencode_cli.log", events)) == [("reference", "assistant_command")]


def test_opencode_webfetch_is_flagged(tmp_path):
    events = _opencode("webfetch", {"url": "https://issues.apache.org/jira/browse/KAFKA-13407"}, "...")
    assert _rules(_jsonl(tmp_path, "opencode_cli.log", events)) == [("issue_key", "assistant_command")]


def test_opencode_line_numbers_are_not_flagged(tmp_path):
    events = _opencode("read", {"filePath": "/w/Fetcher.java", "offset": 13400, "limit": 20}, LISTING)
    assert _rules(_jsonl(tmp_path, "opencode_cli.log", events)) == []


# --- identifiers --------------------------------------------------------------------------------


def test_identifiers_from_links():
    ids = bug_identifiers(
        "zookeeper-2845",
        "[ZOOKEEPER-2845] Data inconsistency issue due to retain database in leader election - ASF JIRA",
        "ZOOKEEPER-2845: Apply commit log when restarting server. · apache/zookeeper@ffb81b9",
    )
    assert ids.numbers == ("2845",) and ids.commits == ("ffb81b9",)
    ids = bug_identifiers("kubernetes-136026", "https://github.com/kubernetes/kubernetes/issues/134939",
                          "https://github.com/kubernetes/kubernetes/pull/136026")
    assert ids.numbers == ("134939", "136026")


def test_numbers_in_the_task_are_ignored(tmp_path):
    bug = SimpleNamespace(**{**vars(BUG), "task_prompt": "Title: Hang\n\nSee the discussion in #11563."})
    path = _trajectory(tmp_path, _chat_call(CURL_FIX), _chat_output(""))
    assert scan_trajectory(path, "kafka-13407", bug) == []


def test_fix_commit_in_git_log_is_flagged(tmp_path):
    bug = SimpleNamespace(issue_link="ZOOKEEPER-2845", fix_link="apache/zookeeper@ffb81b9", task_prompt="Title: x")
    path = _trajectory(tmp_path, _chat_call("git log --oneline -3"), _chat_output("ffb81b9e2 Apply commit log\n0a1b2c3 x"))
    assert [m["rule"] for m in scan_trajectory(path, "zookeeper-2845", bug)] == ["commit"]


def test_cli_scans_every_format_and_quarantines(tmp_path, capsys):
    results = tmp_path / "results"
    for run, (name, events) in {
        "run-01": ("codex_exec.log", _codex(CURL_FIX, "{}")),
        "run-02": ("opencode_cli.log", _opencode("read", {"filePath": "/w/F.java"}, LISTING)),
    }.items():
        (results / "kafka-13407" / run).mkdir(parents=True)
        _jsonl(results / "kafka-13407" / run, name, events)
    (results / "kafka-13407" / "run-03").mkdir()
    _trajectory(results / "kafka-13407" / "run-03", _chat_call("git log --grep 13407"), _chat_output(""))

    assert leakage.main([str(results), "--benchmark", str(ROOT), "--quarantine"]) == 0
    report = json.loads((results / "leakage.json").read_text())
    assert report["scanned"] == 3 and sorted(report["runs"]) == ["kafka-13407/run-01", "kafka-13407/run-03"]
    assert sorted(p.name for p in (results / "_flagged" / "kafka-13407").iterdir()) == ["run-01", "run-03"]
    assert "2 name the original issue" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["git log --grep=13407", "gh pr view 11563", "echo 'see issue 13407'"])
def test_typed_identifiers_are_flagged(tmp_path, command):
    path = _trajectory(tmp_path, _chat_call(command), _chat_output(""))
    assert scan_trajectory(path, "kafka-13407", BUG)


@pytest.mark.parametrize("command", [
    'grep -rn "13407\\|FetchSession" core/src',  # grep alternation
    'grep -R "KAFKA-13407" -n',  # quoted key inside JSON-encoded arguments
    "git log --all --oneline | grep -i '11562\\|11563\\|11564'",
])
def test_identifiers_in_escaped_arguments_are_flagged(tmp_path, command):
    path = _trajectory(tmp_path, _chat_call(command), _chat_output(""))
    assert scan_trajectory(path, "kafka-13407", BUG)


def test_repo_qualified_reference_in_git_log_is_flagged(tmp_path):
    log = "commit 1a2b3c4\n    Fix fetch ordering (#11000)\n\n    close apache/kafka#13407\n"
    path = _trajectory(tmp_path, _chat_call("git log -5"), _chat_output(log))
    assert _rules(path) == [("reference", "tool_output")]


def test_workspace_paths_named_after_the_bug_are_not_flagged(tmp_path):
    path = _trajectory(tmp_path, _chat_call("cd /work/kafka-13407-9cb365 && python3 kafka-13407-9cb365/check.py"),
                       _chat_output(""))
    assert _rules(path) == []

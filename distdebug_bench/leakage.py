"""Leakage guard: flag attempts in which the agent saw or typed the bug's issue or fix.

The task never names the original issue (e.g. ``KAFKA-13407``), its number, the fix
pull request, or the fix commit. If any of them shows up in what the agent wrote
or read, the agent probably looked the answer up, so the attempt is discarded and
rerun. The task prompt is never scanned, and numbers that already occur in the
task text are ignored.

Rules, applied to every trace format below:

* the issue key (``KAFKA-13407``, ``kafka_13407``) anywhere the agent wrote or read,
  except inside file paths;
* the issue or fix-PR number in a reference context (``#11563``, ``issues/13407``,
  ``pull/11563``, ``PR 11563``) anywhere the agent wrote or read;
* the bare issue or fix-PR number (4+ digits) in a command the agent ran or in its
  own text, unless it reads as a line number, range, offset or port;
* the fix commit hash anywhere.

Tool output is checked with every rule except the bare number, so a source listing
whose line numbers happen to include the issue number is not flagged.

Supported traces (one per run directory):

* ``trajectory.json`` written by Mini-SWE-Agent: Responses API items
  (``function_call`` / ``function_call_output``), Chat Completions tool calls
  (assistant ``tool_calls`` / ``role: tool``), and the legacy text format;
* ``codex_exec.log``: ``codex exec --json`` events;
* ``opencode_cli.log``: ``opencode run --format json`` events.

Usage:
    python -m distdebug_bench.leakage results/mini-swe-agent-gpt-5.4 [--benchmark .] [--quarantine]

``--quarantine`` moves flagged run directories to ``<results>/_flagged/``;
running ``distdebug_bench.run`` again then replaces them with fresh attempts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from distdebug_bench.benchmark import get_bug

# Extra names under which a system's issues are referenced.
SYSTEM_ALIASES = {"ignite3": ["ignite"]}

TRACE_FILES = ("trajectory.json", "codex_exec.log", "opencode_cli.log")

COMMAND_SCOPES = {"assistant_command"}
TEXT_SCOPES = {"assistant_reasoning", "assistant_output", "reasoning_summary"}
OUTPUT_SCOPES = {"tool_output"}
MODEL_VISIBLE = COMMAND_SCOPES | TEXT_SCOPES | OUTPUT_SCOPES

# Opaque or harness-only fields that are never shown to the model as text.
_SKIP_KEYS = {"extra", "encrypted_content", "thought_signature", "signature", "id", "call_id", "tool_call_id"}

# A number right after one of these reads as a line number, range bound, offset or port.
_NUMERIC_CONTEXT = re.compile(
    r"(?:\b(?:lines?|ln|lineno|line_number|start_line|end_line|nr|offset|limit|head|tail|port)\b[\"']?\s*[:=<>]*\s*"
    r"|(?<![\w-])-[nABC]\s*|--lines[= ]\s*)$",
    re.IGNORECASE,
)
_REFERENCE = (
    r"(?:(?<!&)#"
    r"|\b(?:issues?|pulls?|pull-requests|merge_requests|commits?)/"
    r"|\b(?:PR|pr|pull request|issue|Issue)\s*#?\s*"
    r"|\b(?:pr|issue)\s+(?:view|diff|checkout)\s+)"
)


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            if key not in _SKIP_KEYS:
                yield from _strings(item)


def _text(value: Any) -> str:
    return "\n".join(s for s in _strings(value) if s)


def _tool_output_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return value
        if isinstance(parsed, dict):
            for key in ("raw_output", "output"):
                if isinstance(parsed.get(key), str):
                    return parsed[key]
        return _text(parsed)
    return _text(value)


def _command_text(value: Any) -> str:
    """Tool-call arguments are usually a JSON string; scan the decoded values, not the escaped JSON."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    return _text(value)


def _is_tool_output(message: dict[str, Any], index: int) -> bool:
    if message.get("type") == "function_call_output" or message.get("role") == "tool":
        return True
    # Legacy text format: observations come back as user messages after the task.
    return message.get("role") == "user" and index > 1


class _Records(list):
    def add(self, scope: str, value: Any) -> None:
        decode = {"tool_output": _tool_output_text, "assistant_command": _command_text}.get(scope, _text)
        if text := decode(value):
            self.append((scope, text))


def text_records(trajectory: dict[str, Any]) -> list[tuple[str, str]]:
    """(scope, text) pairs for everything in a Mini-SWE-Agent trajectory the model could see or produce."""
    records = _Records()
    add = records.add
    for index, message in enumerate(trajectory.get("messages") or []):
        if not isinstance(message, dict):
            continue
        role, kind = message.get("role"), message.get("type")
        if _is_tool_output(message, index):
            add("tool_output", message.get("output"))
            add("tool_output", message.get("content"))
        elif role in {"system", "user"}:
            add("prompt", message.get("content"))
        elif role == "assistant":
            # content plus provider-specific reasoning fields (reasoning_content, thinking_blocks, ...)
            add("assistant_reasoning", {k: v for k, v in message.items() if k not in {"role", "tool_calls", "function_call"}})
            for call in message.get("tool_calls") or []:
                if isinstance(call, dict):
                    add("assistant_command", (call.get("function") or {}).get("arguments"))
            if isinstance(message.get("function_call"), dict):
                add("assistant_command", message["function_call"].get("arguments"))
        elif role == "exit":
            add("assistant_output", message.get("content"))
            add("assistant_output", (message.get("extra") or {}).get("submission"))
        elif kind == "function_call":
            add("assistant_command", message.get("arguments"))
        for item in message.get("output") if isinstance(message.get("output"), list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "reasoning":
                add("reasoning_summary", item.get("summary"))
                add("reasoning_summary", item.get("content"))
            elif item.get("type") == "function_call":
                add("assistant_command", item.get("arguments"))
                add("assistant_command", item.get("name"))
            elif item.get("type") == "message":
                add("assistant_output", item.get("content"))
        if isinstance(message.get("reasoning"), dict):
            add("reasoning_summary", message["reasoning"].get("summary"))
    return list(records)


def _json_lines(lines: Iterable[str]) -> Iterable[dict[str, Any]]:
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def codex_records(lines: Iterable[str]) -> list[tuple[str, str]]:
    """(scope, text) pairs from ``codex exec --json`` output."""
    items: dict[str, dict[str, Any]] = {}
    for event in _json_lines(lines):
        item = event.get("item")
        if isinstance(item, dict) and str(event.get("type", "")).startswith("item."):
            items[str(item.get("id", len(items)))] = item  # the last update of an item wins
    records = _Records()
    for item in items.values():
        kind = item.get("type")
        if kind == "reasoning":
            records.add("reasoning_summary", item.get("text"))
        elif kind in {"agent_message", "todo_list"}:
            records.add("assistant_output", item.get("text") or item.get("items"))
        elif kind == "command_execution":
            records.add("assistant_command", item.get("command"))
            records.add("tool_output", item.get("aggregated_output"))
        elif kind == "web_search":
            records.add("assistant_command", item.get("query"))
        elif kind == "mcp_tool_call":
            records.add("assistant_command", item.get("arguments"))
            records.add("tool_output", [item.get("result"), item.get("error")])
        elif kind == "file_change":
            records.add("assistant_command", item.get("changes"))
        else:
            records.add("tool_output", {k: v for k, v in item.items() if k != "type"})
    return list(records)


def opencode_records(lines: Iterable[str]) -> list[tuple[str, str]]:
    """(scope, text) pairs from ``opencode run --format json`` output."""
    records = _Records()
    for event in _json_lines(lines):
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        if part.get("type") == "tool":
            state = part.get("state") or {}
            records.add("assistant_command", state.get("input"))
            records.add("tool_output", [state.get("output"), state.get("error")])
        elif part.get("type") == "text":
            records.add("assistant_output", part.get("text"))
        elif part.get("type") == "reasoning":
            records.add("reasoning_summary", part.get("text"))
    return list(records)


def trace_records(path: Path) -> list[tuple[str, str]]:
    """(scope, text) pairs for one trace file, dispatching on its name."""
    if path.name == "codex_exec.log":
        return codex_records(path.read_text(errors="replace").splitlines())
    if path.name == "opencode_cli.log":
        return opencode_records(path.read_text(errors="replace").splitlines())
    return text_records(json.loads(path.read_text()))


@dataclass(frozen=True)
class BugIdentifiers:
    """What the agent must not see: issue key patterns, issue and fix-PR numbers, fix commits."""

    keys: tuple[re.Pattern[str], ...]
    numbers: tuple[str, ...]
    commits: tuple[str, ...]


def issue_patterns(bug_name: str) -> list[re.Pattern[str]]:
    """Patterns for ``<system>-<number>`` style identifiers of this bug."""
    system, _, number = bug_name.rpartition("-")
    aliases = {system, *SYSTEM_ALIASES.get(system, [])}  # patterns ignore case
    return [
        re.compile(rf"(?<![A-Za-z0-9]){re.escape(a)}[-_ ]{re.escape(number)}(?![A-Za-z0-9-])", re.IGNORECASE)
        for a in sorted(aliases)
    ]


def bug_identifiers(bug_name: str, issue_link: str = "", fix_link: str = "", prompt: str = "") -> BugIdentifiers:
    """Identifiers of one bug; numbers and commits that occur in ``prompt`` are dropped."""
    numbers = {bug_name.rpartition("-")[2]}
    numbers |= set(re.findall(r"/issues/(\d+)", issue_link))
    numbers |= set(re.findall(r"\b[A-Z][A-Z0-9]+-(\d+)", issue_link))
    numbers |= set(re.findall(r"(?:/pulls?/|Pull Request #|\bPR #?)(\d+)", fix_link))
    commits = set(re.findall(r"(?:@|/commits?/)([0-9a-f]{7,40})\b", fix_link))
    numbers = {n for n in numbers if not re.search(rf"(?<![0-9]){n}(?![0-9])", prompt)}
    commits = {c for c in commits if c[:7] not in prompt}
    return BugIdentifiers(tuple(issue_patterns(bug_name)), tuple(sorted(numbers)), tuple(sorted(commits)))


def _in_path_context(text: str, start: int, end: int) -> bool:
    """Whether a match is part of a local file path (e.g. a results directory) rather than a URL or prose."""
    token_start = max(text.rfind(c, 0, start) for c in " \t\n\"'(<=") + 1
    if "://" in text[token_start:start]:
        return False
    if (start > 0 and text[start - 1] == "/") or (end < len(text) and text[end] == "/"):
        return True
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : line_end if line_end != -1 else len(text)]
    return any(marker in line for marker in ("trajectory_path", "diagnosis_path", "output_path", "/home/", "results/"))


def _matches(scope: str, text: str, ids: BugIdentifiers) -> Iterable[tuple[str, re.Match[str]]]:
    for pattern in ids.keys:
        for m in pattern.finditer(text):
            if not _in_path_context(text, m.start(), m.end()):
                yield "issue_key", m
    for number in ids.numbers:
        for m in re.finditer(rf"{_REFERENCE}{number}(?![0-9])", text):
            yield "reference", m
        if scope in OUTPUT_SCOPES or len(number) < 4:
            continue
        for m in re.finditer(rf"(?<![\w.,:+\-/#&]){number}(?![\w.,:\-/])", text):
            if not _NUMERIC_CONTEXT.search(text[max(0, m.start() - 24) : m.start()]):
                yield "number", m
    for commit in ids.commits:
        for m in re.finditer(rf"(?<![0-9A-Za-z]){commit[:7]}[0-9a-f]*(?![0-9A-Za-z])", text, re.IGNORECASE):
            yield "commit", m


def scan_records(records: Iterable[tuple[str, str]], ids: BugIdentifiers) -> list[dict[str, str]]:
    """Leakage matches in (scope, text) records; prompt records are skipped."""
    matches = []
    for scope, text in records:
        if scope not in MODEL_VISIBLE:
            continue
        seen: set[int] = set()
        for rule, m in sorted(_matches(scope, text, ids), key=lambda rule_match: rule_match[1].start()):
            if m.start() in seen:
                continue
            seen.add(m.start())
            snippet = text[max(0, m.start() - 80) : m.end() + 80].replace("\n", "\\n")
            matches.append({"scope": scope, "rule": rule, "match": m.group(0), "snippet": snippet})
    return matches


def scan_trajectory(path: Path, bug_name: str, bug: Any = None) -> list[dict[str, str]]:
    """Leakage matches in one trace (``trajectory.json``, ``codex_exec.log`` or ``opencode_cli.log``).

    ``bug`` (a :class:`~distdebug_bench.models.BugReport`) adds the fix-PR number, the fix
    commit and the task text; without it only the issue key and number are checked.
    """
    records = trace_records(Path(path))
    prompt = "\n".join(text for scope, text in records if scope == "prompt")
    if bug is not None:
        ids = bug_identifiers(bug_name, bug.issue_link, bug.fix_link, prompt + "\n" + bug.task_prompt)
    else:
        ids = bug_identifiers(bug_name, prompt=prompt)
    return scan_records(records, ids)


def run_traces(results: Path) -> list[Path]:
    """The trace file of every run directory under ``results`` (``_flagged`` excluded)."""
    traces = []
    for run_dir in sorted(results.glob("*/run-*")):
        if run_dir.parts[-2] == "_flagged" or not run_dir.is_dir():
            continue
        found = [run_dir / name for name in TRACE_FILES if (run_dir / name).exists()]
        if found:
            traces.append(found[0])
    return traces


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, help="Results directory written by distdebug_bench.run")
    parser.add_argument("--benchmark", type=Path, default=Path("."), help="Repository root containing benchmark/")
    parser.add_argument("--quarantine", action="store_true", help="Move flagged runs to <results>/_flagged/")
    args = parser.parse_args(argv)

    flagged, bugs = {}, {}
    traces = run_traces(args.results)
    for path in traces:
        bug_name, run = path.parts[-3], path.parts[-2]
        if bug_name not in bugs:
            try:
                bugs[bug_name] = get_bug(args.benchmark, bug_name)
            except FileNotFoundError:
                print(f"warning: {bug_name} is not under {args.benchmark}; checking its issue key and number only")
                bugs[bug_name] = None
        if matches := scan_trajectory(path, bug_name, bugs[bug_name]):
            flagged[f"{bug_name}/{run}"] = matches
    report = {"scanned": len(traces), "flagged": len(flagged), "runs": flagged}
    (args.results / "leakage.json").write_text(json.dumps(report, indent=2))
    print(f"Scanned {len(traces)} traces; {len(flagged)} name the original issue or its fix.")
    for run, matches in flagged.items():
        first = matches[0]
        print(f"  {run}: {first['match']} ({first['rule']}) in {first['scope']}: {first['snippet'][:160]}")
        if args.quarantine:
            target = args.results / "_flagged" / run
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(args.results / run), target)
    if flagged and args.quarantine:
        print(f"Moved flagged runs to {args.results / '_flagged'}; rerun distdebug_bench.run to replace them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Leakage guard: flag attempts whose model-visible text names the original issue.

The agent never sees the issue identifier (e.g. ``KAFKA-13407``). If it appears in
the model's reasoning, commands, or tool output, the agent likely retrieved the
answer (e.g. from a commit message), so the paper discards and reruns the attempt.
The prompt and path-like contexts are not scanned.

Usage:
    python -m distdebug_bench.leakage results/mini-swe-agent-gpt-5.4 [--quarantine]

``--quarantine`` moves flagged run directories to ``<results>/_flagged/``;
running ``distdebug_bench.run`` again then replaces them with fresh attempts.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any, Iterable

# Extra names under which a system's issues are referenced.
SYSTEM_ALIASES = {"ignite3": ["ignite"]}

MODEL_VISIBLE = {"assistant_reasoning", "assistant_output", "assistant_command", "reasoning_summary", "tool_output"}


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
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


def _is_tool_output(message: dict[str, Any], index: int) -> bool:
    if message.get("type") == "function_call_output":
        return True
    if message.get("role") != "user":
        return False
    content = str(message.get("content") or "").lstrip()
    return index > 1 and (
        content.startswith("<returncode>") or content.startswith("<output>") or "function_call_output" in content[:200]
    )


def text_records(trajectory: dict[str, Any]) -> list[tuple[str, str]]:
    """(scope, text) pairs for everything in a trajectory the model could see or produce."""
    records: list[tuple[str, str]] = []

    def add(scope: str, value: Any) -> None:
        if text := (_tool_output_text(value) if scope == "tool_output" else _text(value)):
            records.append((scope, text))

    for index, message in enumerate(trajectory.get("messages") or []):
        if not isinstance(message, dict):
            continue
        tool_output = _is_tool_output(message, index)
        if message.get("role") in {"system", "user"} and not tool_output:
            add("prompt", message.get("content"))
        elif message.get("role") == "assistant":
            add("assistant_reasoning", message.get("content"))
        elif tool_output:
            add("tool_output", message.get("output"))
            add("tool_output", message.get("content"))
        if message.get("type") == "function_call_output" and not tool_output:
            add("tool_output", message.get("output"))
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
    return records


def issue_patterns(bug_name: str) -> list[re.Pattern[str]]:
    """Patterns for ``<system>-<number>`` style identifiers of this bug."""
    system, _, number = bug_name.rpartition("-")
    aliases = {system, *SYSTEM_ALIASES.get(system, [])}  # patterns ignore case
    return [
        re.compile(rf"(?<![A-Za-z0-9]){re.escape(a)}[-_ ]{re.escape(number)}(?![A-Za-z0-9-])", re.IGNORECASE)
        for a in sorted(aliases)
    ]


def _in_path_context(text: str, start: int, end: int) -> bool:
    if (start > 0 and text[start - 1] in "/\\") or (end < len(text) and text[end] in "/\\"):
        return True
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : line_end if line_end != -1 else len(text)]
    return any(marker in line for marker in ("trajectory_path", "diagnosis_path", "output_path", "/home/", "results/"))


def scan_trajectory(path: Path, bug_name: str) -> list[dict[str, str]]:
    """Matches of the bug's identifier in model-visible text of one trajectory."""
    matches = []
    for scope, text in text_records(json.loads(path.read_text())):
        if scope not in MODEL_VISIBLE:
            continue
        for pattern in issue_patterns(bug_name):
            for m in pattern.finditer(text):
                if not _in_path_context(text, m.start(), m.end()):
                    snippet = text[max(0, m.start() - 80) : m.end() + 80].replace("\n", "\\n")
                    matches.append({"scope": scope, "match": m.group(0), "snippet": snippet})
    return matches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", type=Path, help="Results directory written by distdebug_bench.run")
    parser.add_argument("--quarantine", action="store_true", help="Move flagged runs to <results>/_flagged/")
    args = parser.parse_args(argv)

    flagged = {}
    trajectories = sorted(p for p in args.results.glob("*/run-*/trajectory.json") if p.parts[-3] != "_flagged")
    for path in trajectories:
        bug_name, run = path.parts[-3], path.parts[-2]
        if matches := scan_trajectory(path, bug_name):
            flagged[f"{bug_name}/{run}"] = matches
    report = {"scanned": len(trajectories), "flagged": len(flagged), "runs": flagged}
    (args.results / "leakage.json").write_text(json.dumps(report, indent=2))
    print(f"Scanned {len(trajectories)} trajectories; {len(flagged)} name the original issue.")
    for run, matches in flagged.items():
        print(f"  {run}: {matches[0]['match']} in {matches[0]['scope']}: {matches[0]['snippet'][:160]}")
        if args.quarantine:
            target = args.results / "_flagged" / run
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(args.results / run), target)
    if flagged and args.quarantine:
        print(f"Moved flagged runs to {args.results / '_flagged'}; rerun distdebug_bench.run to replace them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

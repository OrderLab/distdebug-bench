#!/usr/bin/env python3
"""Classify bugs by description richness using only bug-report-visible features.

Features are extracted purely from the bug description and artifacts — no ground
truth is used.  The output is a JSON file keyed by bug name with raw features
and a composite richness score.

Usage:
    python classify_bugs.py                        # default output to stdout
    python classify_bugs.py -o bug_classifications.json
    python classify_bugs.py --table                # print a human-readable table
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

BUGS_DIR = Path(__file__).resolve().parent / "bugs"

# ── Identifiers we should ignore when counting code landmarks ────────────────
# Common Java / Go / Python standard library names that appear as CamelCase
# and would inflate the count without providing navigational value.
_COMMON_CAMELCASE: set[str] = {
    "Exception",
    "RuntimeException",
    "NullPointerException",
    "IllegalArgumentException",
    "IllegalStateException",
    "IOException",
    "InterruptedException",
    "TimeoutException",
    "ConcurrentModificationException",
    "ClassNotFoundException",
    "NoSuchElementException",
    "UnsupportedOperationException",
    "StackOverflowError",
    "OutOfMemoryError",
    "FileNotFoundException",
    "SecurityException",
    "InputStream",
    "OutputStream",
    "HashMap",
    "ArrayList",
    "LinkedList",
    "HashSet",
    "TreeMap",
    "StringBuilder",
    "StringBuffer",
    "ByteBuffer",
    "Boolean",
    "Integer",
    "CompletableFuture",
    "ExecutorService",
    "ScheduledExecutorService",
    "ThreadPoolExecutor",
    "CountDownLatch",
    "AtomicInteger",
    "AtomicLong",
    "AtomicBoolean",
    "ReentrantLock",
    "ReadWriteLock",
    "ConcurrentHashMap",
    "BlockingQueue",
    "LinkedBlockingQueue",
    "ArrayBlockingQueue",
    "FutureTask",
    "StackTrace",
    "PrintWriter",
    "BufferedReader",
    "ObjectMapper",
    "JsonNode",
    # Go common types
    "Context",
    "WaitGroup",
    "RWMutex",
    # Generic words that happen to be CamelCase
    "GitHub",
    "ZooKeeper",
    "TiFlash",
    "MacBook",
    "IntelliJ",
    "StackOverflow",
    "JavaScript",
    "TypeScript",
    "PostgreSQL",
    "QuickStart",
}

# JIRA-key pattern: PROJECT-1234
_JIRA_RE = re.compile(r"\b([A-Z][A-Z0-9_]+-\d+)\b")

# GitHub-style issue/PR reference: #123
_GH_REF_RE = re.compile(r"(?<!\w)#(\d+)\b")

# Causal introduction phrases
_CAUSAL_PHRASE_RE = re.compile(
    r"\b(?:introduced|caused|regression|regressed|broke|broken|fixed)\s+"
    r"(?:in|by|from|after)\b",
    re.IGNORECASE,
)

# CamelCase identifiers (at least two humps): e.g. RemoteLogManager, buildRetentionSizeData
_CAMELCASE_RE = re.compile(r"\b([A-Z][a-z]+(?:[A-Z][a-z0-9]*)+)\b")

# Method call pattern: foo.bar( or Foo.bar(
_METHOD_CALL_RE = re.compile(r"\b(\w+\.\w+)\(")

# snake_case identifiers (at least one underscore, all lower): e.g. copy_log_segment
_SNAKE_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")

# ── Rule-targeted keyword sets ───────────────────────────────────────────────
# Each set corresponds to a rule cluster in the system prompt.  We count unique
# keyword hits in the prose to detect whether the bug's archetype matches the
# rule's purpose.

# State Lifecycle + Hierarchy + Causative Origin rules:
#   Bugs about stale/inconsistent state across restarts, transitions, or
#   multi-component lifecycle management.
_STATE_KEYWORDS: set[str] = {
    "restart",
    "restarted",
    "restarting",
    "shutdown",
    "shut down",
    "stale",
    "inconsistent",
    "inconsistency",
    "retained",
    "retain",
    "cache",
    "cached",
    "caching",
    "lifecycle",
    "life cycle",
    "recovery",
    "recover",
    "reconnect",
    "reconnection",
    "re-establish",
    "initialization",
    "initialize",
    "reinitialize",
    "cleanup",
    "clean up",
    "teardown",
    "tear down",
    "persist",
    "persisted",
    "persistence",
    "snapshot",
    "election",
    "failover",
    "leader",
    "follower",
    "quorum",
    "expire",
    "expired",
    "session",
    "state",
    "transition",
}

# Backward Caller Tracing rule:
#   Bugs where the description points at a crash site / symptom and the root
#   cause is upstream.  Detected by crash-oriented language.
_CRASH_KEYWORDS: set[str] = {
    "crash",
    "crashed",
    "crashes",
    "npe",
    "nullpointerexception",
    "null pointer",
    "segfault",
    "sigsegv",
    "sigabrt",
    "abort",
    "aborted",
    "panic",
    "assertion",
    "assert",
    "thrown",
    "throws",
    "exception",
    "stack trace",
    "stacktrace",
    "oom",
    "out of memory",
    "core dump",
}

_STATE_KW_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_STATE_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_CRASH_KW_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_CRASH_KEYWORDS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_COMMON_SNAKE: set[str] = {
    "bug_report",
    "root_cause",
    "stack_trace",
    "log_file",
    "time_out",
    "set_up",
    "shut_down",
    "num_records",
    "record_size",
    "batch_size",
    "bootstrap_servers",
    "replication_factor",
    "min_workers",
    "max_workers",
}

# ── Log / stack-trace line detection ─────────────────────────────────────────

# Timestamp-prefixed log lines:
#   2021-10-27 09:13:08,882  or  I0602 18:09:25.494333  or  Oct 27 09:13:08
_LOG_LINE_RE = re.compile(
    r"^(?:"
    r"\d{4}[-/]\d{2}[-/]\d{2}[\sT]\d{2}:\d{2}"  # ISO-ish: 2021-10-27 09:13:08
    r"|[A-Z]\d{4}\s+\d{2}:\d{2}:\d{2}"  # glog style: I0602 18:09:25
    r"|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"  # syslog: Oct 27 09:13:08
    r"|[A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}"  # Java util logging: Apr 23, 2025 10:19:23 AM
    r"|py\d+.*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}"  # Python env prefix + timestamp
    r")",
    re.MULTILINE,
)

# Java / Python stack trace lines:  "at org.foo.Bar.method(File.java:123)"
# or "  File \"/path/to/file.py\", line 123, in method"
_STACK_LINE_RE = re.compile(
    r"^(?:"
    r"\s*at\s+[\w.$/@]+\([\w.]+:\d+\)"  # Java: at pkg.Class.method(File.java:42) or java.base/pkg.Class(File.java:42)
    r"|\s*at\s+[\w.$/@]+\([\w. ]+\)"  # Java: at pkg.Class.method(Native Method)
    r"|\s*File\s+\"[^\"]+\",\s+line\s+\d+"  # Python: File "foo.py", line 42
    r"|\s*@\s+0x[0-9a-f]+"  # Native: @ 0x7f2768732002 func()
    r"|Caused\s+by:\s+"  # Java chained exception
    r"|\.{3}\s+\d+\s+more"  # Java: ... 10 more
    r"|java\.\w+\.\w+Exception:"  # Java exception line: java.util.concurrent.TimeoutException:
    r"|java\.\w+\.\w+Error:"  # Java error line: java.lang.OutOfMemoryError:
    r"|java\.lang\.Thread\.State:\s+"  # Thread state line
    r")",
    re.MULTILINE,
)

# Signal/crash markers
_CRASH_LINE_RE = re.compile(
    r"^\*{3}\s+(?:Aborted|SIGSEGV|SIGABRT|SIGBUS|SIGFPE|SIGILL)",
    re.MULTILINE,
)

# Command-line / shell snippets that should be excluded from prose
_SHELL_CMD_RE = re.compile(
    r"^(?:"
    r"\s*(?:bin/|\./)[\w/.-]+"  # bin/kafka-topics.sh ...
    r"|.*\$\s*\{.*\}"  # shell variable expansion
    r"|\s*(?:cat|echo|export|mkdir|watch|curl|kubectl|docker)\s+"  # common shell cmds
    r")",
    re.MULTILINE,
)

# Code block fences
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)


def _classify_line(line: str) -> str:
    """Classify a single line as 'log', 'stack', 'shell', or 'prose'."""
    stripped = line.strip()
    if not stripped:
        return "prose"
    if _LOG_LINE_RE.match(stripped):
        return "log"
    if _STACK_LINE_RE.match(line):  # use original line to preserve leading whitespace
        return "stack"
    if _CRASH_LINE_RE.match(stripped):
        return "stack"
    return "prose"


def _split_description(text: str) -> tuple[str, str, str]:
    """Split description into (prose, log_text, stack_text).

    Returns prose text with log/stack/code-block sections removed, plus the
    extracted log and stack text separately.
    """
    lines = text.split("\n")
    prose_lines: list[str] = []
    log_lines: list[str] = []
    stack_lines: list[str] = []

    in_code_block = False
    # Track consecutive non-prose lines so we can absorb short non-prose runs
    # that are actually inline references rather than blocks.

    for line in lines:
        # Handle code fences — content inside ``` blocks is not prose
        if _CODE_FENCE_RE.match(line.strip()):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            # Code blocks might contain logs or stack traces; classify them
            kind = _classify_line(line)
            if kind == "log":
                log_lines.append(line)
            elif kind == "stack":
                stack_lines.append(line)
            # Either way, not prose
            continue

        kind = _classify_line(line)
        if kind == "log":
            log_lines.append(line)
        elif kind == "stack":
            stack_lines.append(line)
        else:
            prose_lines.append(line)

    return "\n".join(prose_lines), "\n".join(log_lines), "\n".join(stack_lines)


def _detect_has_logs(log_text: str) -> bool:
    """Return True if log_text contains at least one log line."""
    lines = [l for l in log_text.split("\n") if l.strip()]
    return len(lines) >= 1


def _detect_has_stack_traces(stack_text: str) -> bool:
    """Return True if stack_text contains at least one stack frame."""
    lines = [l for l in stack_text.split("\n") if l.strip()]
    return len(lines) >= 1


def _extract_code_identifiers(text: str) -> list[str]:
    """Extract unique code-relevant identifiers from text."""
    ids: set[str] = set()

    # CamelCase identifiers
    for m in _CAMELCASE_RE.finditer(text):
        name = m.group(1)
        if name not in _COMMON_CAMELCASE:
            ids.add(name)

    # Method calls: capture "Class.method" as a single identifier
    for m in _METHOD_CALL_RE.finditer(text):
        ids.add(m.group(1))

    # snake_case identifiers
    for m in _SNAKE_RE.finditer(text):
        name = m.group(1)
        if name not in _COMMON_SNAKE:
            ids.add(name)

    return sorted(ids)


def _extract_causal_refs(text: str) -> tuple[list[str], list[str], int]:
    """Extract JIRA refs, GitHub issue refs, and causal phrase count."""
    jira = sorted(set(_JIRA_RE.findall(text)))
    gh = sorted(set(_GH_REF_RE.findall(text)))
    phrases = len(_CAUSAL_PHRASE_RE.findall(text))
    return jira, gh, phrases


def _count_keyword_hits(text: str, pattern: re.Pattern[str]) -> tuple[int, list[str]]:
    """Count unique keyword hits and return (count, list_of_unique_keywords)."""
    hits = set(m.group(1).lower() for m in pattern.finditer(text))
    return len(hits), sorted(hits)


@dataclass
class BugClassification:
    """Feature vector for a single bug, derived only from description + artifacts."""

    name: str

    # Feature 1: raw description length (full text including logs/stacks)
    desc_length: int = 0
    # Length of only the prose portion (reporter's own words)
    prose_length: int = 0

    # Feature 2: number of artifact files
    n_artifacts: int = 0

    # Feature 3: evidence types — detected from the text, not from labels
    has_logs: bool = False
    has_stack_traces: bool = False
    has_analysis: bool = False

    # Feature 4: causal references — things that can be resolved via git log
    jira_refs: list[str] = field(default_factory=list)
    gh_issue_refs: list[str] = field(default_factory=list)
    causal_phrases: int = 0
    n_causal_refs: int = 0  # total: len(jira_refs) + len(gh_issue_refs) + causal_phrases

    # Feature 5: code identifiers mentioned in PROSE only (unique, deduplicated)
    code_identifiers: list[str] = field(default_factory=list)
    n_code_identifiers: int = 0

    # Feature 6: rule-targeted keyword hits (from prose only)
    #   State keywords: restart, stale, inconsistent, shutdown, lifecycle, etc.
    state_keywords: list[str] = field(default_factory=list)
    n_state_keywords: int = 0
    #   Crash keywords: crash, NPE, panic, exception, abort, etc.
    crash_keywords: list[str] = field(default_factory=list)
    n_crash_keywords: int = 0

    # Composite score
    description_richness: float = 0.0


def classify_bug(bug_dir: Path, system: str, bug_id: str) -> BugClassification:
    """Classify a single bug from its report.json and artifact_dir."""
    report_path = bug_dir / "report.json"
    with open(report_path) as f:
        report = json.load(f)

    name = f"{system}-{bug_id}"
    desc = report.get("description", "")

    # Split description into prose vs log/stack sections
    prose, log_text, stack_text = _split_description(desc)

    # Feature 1: description lengths
    desc_length = len(desc)
    prose_length = len(prose)

    # Feature 2: artifact count
    art_dir = bug_dir / "artifact_dir"
    n_artifacts = 0
    if art_dir.is_dir():
        n_artifacts = sum(1 for p in art_dir.iterdir() if p.is_file())

    # Feature 3: evidence types — detected from actual text content
    has_logs = _detect_has_logs(log_text)
    has_stack_traces = _detect_has_stack_traces(stack_text)
    has_analysis = report.get("contains_analysis", "No") == "Yes"

    # Feature 4: causal references (from prose only — refs in logs are noise)
    jira, gh, causal_phrases = _extract_causal_refs(prose)
    n_causal_refs = len(jira) + len(gh) + causal_phrases

    # Feature 5: code identifiers from PROSE only (not from logs/stacks)
    code_ids = _extract_code_identifiers(prose)
    n_code_identifiers = len(code_ids)

    # Feature 6: rule-targeted keyword hits from prose
    n_state_kw, state_kws = _count_keyword_hits(prose, _STATE_KW_RE)
    n_crash_kw, crash_kws = _count_keyword_hits(prose, _CRASH_KW_RE)

    # Composite score: weighted sum of normalized features.
    # Artifact files are concrete evidence sources, while raw identifier counts
    # from prose can overstate grounding for stack-trace-heavy bug reports, so
    # routing uses artifact evidence instead of code-identifier density.
    richness = (
        min(desc_length / 2000.0, 3.0)  # cap at 3; longer desc = more info, diminishing
        + n_artifacts * 1.0  # each artifact file is a concrete data source
        + has_logs * 1.5  # logs narrow search significantly
        + has_stack_traces * 2.0  # stack traces point directly at code
        + has_analysis * 1.5  # author analysis explains causal chain
        + n_causal_refs * 2.0  # resolvable to commits = very high value
        + min(n_artifacts * 1.0, 3.0)  # capped extra weight for inspectable evidence
    )

    return BugClassification(
        name=name,
        desc_length=desc_length,
        prose_length=prose_length,
        n_artifacts=n_artifacts,
        has_logs=has_logs,
        has_stack_traces=has_stack_traces,
        has_analysis=has_analysis,
        jira_refs=jira,
        gh_issue_refs=gh,
        causal_phrases=causal_phrases,
        n_causal_refs=n_causal_refs,
        code_identifiers=code_ids,
        n_code_identifiers=n_code_identifiers,
        state_keywords=state_kws,
        n_state_keywords=n_state_kw,
        crash_keywords=crash_kws,
        n_crash_keywords=n_crash_kw,
        description_richness=round(richness, 2),
    )


def classify_all(bugs_dir: Path) -> dict[str, BugClassification]:
    """Classify all bugs in the benchmark."""
    results: dict[str, BugClassification] = {}
    for system_dir in sorted(bugs_dir.iterdir()):
        if not system_dir.is_dir():
            continue
        system = system_dir.name
        for bug_dir in sorted(system_dir.iterdir()):
            if not bug_dir.is_dir():
                continue
            report_path = bug_dir / "report.json"
            if not report_path.exists():
                continue
            bug_id = bug_dir.name
            cls = classify_bug(bug_dir, system, bug_id)
            results[cls.name] = cls
    return results


def print_table(classifications: dict[str, BugClassification]) -> None:
    """Print a human-readable summary table."""
    header = (
        f"{'name':<30} {'len':>5} {'prose':>5} {'art':>3} {'log':>3} {'stk':>3} "
        f"{'ana':>3} {'refs':>4} {'ids':>4} {'st_kw':>5} {'cr_kw':>5} {'richness':>8}"
    )
    print(header)
    print("-" * len(header))
    for cls in sorted(classifications.values(), key=lambda c: -c.description_richness):
        row = (
            f"{cls.name:<30} {cls.desc_length:>5} {cls.prose_length:>5} "
            f"{cls.n_artifacts:>3} "
            f"{1 if cls.has_logs else 0:>3} {1 if cls.has_stack_traces else 0:>3} "
            f"{1 if cls.has_analysis else 0:>3} {cls.n_causal_refs:>4} "
            f"{cls.n_code_identifiers:>4} {cls.n_state_keywords:>5} "
            f"{cls.n_crash_keywords:>5} {cls.description_richness:>8.2f}"
        )
        print(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify bugs by description richness")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: stdout)",
    )
    parser.add_argument(
        "--table",
        action="store_true",
        help="Print a human-readable summary table",
    )
    parser.add_argument(
        "--bugs-dir",
        type=str,
        default=None,
        help="Path to bugs directory (default: auto-detect from script location)",
    )
    args = parser.parse_args()

    bugs_dir = Path(args.bugs_dir) if args.bugs_dir else BUGS_DIR
    if not bugs_dir.exists():
        print(f"Error: bugs directory not found at {bugs_dir}", file=sys.stderr)
        sys.exit(1)

    classifications = classify_all(bugs_dir)

    if args.table:
        print_table(classifications)
    else:
        out = {name: asdict(cls) for name, cls in classifications.items()}
        json_str = json.dumps(out, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(json_str + "\n")
            print(f"Wrote {len(classifications)} classifications to {args.output}")
        else:
            print(json_str)


if __name__ == "__main__":
    main()

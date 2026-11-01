#!/usr/bin/env python3
"""Characterize benchmark bugs using ground-truth-aware difficulty signals.

This script computes two benchmark characterization axes:

1. Textual anchor:
   - method: a non-generic gold function name appears in the issue text.
   - file_class: a gold file stem or class name appears, but no gold method.
   - opaque: no extracted gold identifier appears in the issue text.

2. Crash-to-fix distance:
   - co_located: first project-internal stack frame is in a gold file.
   - adjacent: crash site and a gold file appear in the same nearby package.
   - cross_component: stack trace exists, but the crash site is elsewhere.
   - no_stack_trace: no usable project-internal stack frame is found.

Unlike benchmark/classify_bugs.py, this analysis intentionally uses gold labels.
It is therefore for benchmark characterization, not for issue-only routing.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


GENERIC_METHODS = {
    "add",
    "apply",
    "build",
    "call",
    "check",
    "close",
    "compareTo",
    "connect",
    "create",
    "decode",
    "delete",
    "deserialize",
    "disconnect",
    "doRun",
    "doStart",
    "doStop",
    "encode",
    "equals",
    "execute",
    "get",
    "handle",
    "handleRequest",
    "hashCode",
    "init",
    "initialize",
    "invoke",
    "load",
    "main",
    "notify",
    "open",
    "parse",
    "process",
    "processRequest",
    "put",
    "read",
    "receive",
    "register",
    "remove",
    "reset",
    "run",
    "save",
    "send",
    "serialize",
    "set",
    "setup",
    "start",
    "stop",
    "teardown",
    "test",
    "toString",
    "unregister",
    "update",
    "validate",
    "verify",
    "write",
}

EXTERNAL_JAVA = re.compile(
    r"^(java\.|javax\.|sun\.|com\.sun\.|com\.google\.|io\.netty\.|"
    r"org\.junit\.|org\.slf4j\.|ch\.qos\.|org\.mockito\.|"
    r"org\.hamcrest\.|scala\.|akka\.|play\.|org\.jboss\.|"
    r"org\.eclipse\.|com\.fasterxml\.|org\.springframework\.|"
    r"io\.grpc\.|com\.codahale\.|jdk\.|"
    r"org\.apache\.hbase\.thirdparty\.|"
    r"org\.apache\.hadoop\.thirdparty\.)"
)
EXTERNAL_GO = re.compile(
    r"^(runtime|sync|fmt|os|io|net|context|strings|bytes|encoding|"
    r"reflect|testing|errors|math|sort|time|unicode|path|strconv|"
    r"golang\.org|google\.golang\.org|github\.com/grpc)"
)

ANCHORS = ("method", "file_class", "opaque")
DISTANCES = ("co_located", "adjacent", "cross_component", "no_stack_trace")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def unique_ordered(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def report_text(report: dict[str, Any]) -> str:
    return f"{report.get('title', '')}\n{report.get('description', '')}".strip()


def gt_ids(blocks: list[dict[str, Any]]) -> dict[str, set[str]]:
    methods: set[str] = set()
    file_stems: set[str] = set()
    classes: set[str] = set()
    for block in blocks:
        stem = Path(block.get("file", "")).stem
        if len(stem) > 3:
            file_stems.add(stem)

        function = block.get("function", "")
        if not function:
            continue
        parts = re.split(r"\.|::", function)
        leaf = re.sub(r"<[^>]*>", "", parts[-1]).strip()
        if len(leaf) > 5 and leaf not in GENERIC_METHODS:
            methods.add(leaf)
        if len(parts) > 1 and len(parts[-2]) > 3:
            classes.add(parts[-2])

    return {"methods": methods, "file_stems": file_stems, "classes": classes}


def classify_anchor(report: dict[str, Any], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text = report_text(report)
    ids = gt_ids(blocks)

    matched_methods = sorted(method for method in ids["methods"] if method in text)
    matched_file_or_class = sorted(
        token for token in ids["file_stems"] | ids["classes"] if token in text
    )

    if matched_methods:
        anchor = "method"
    elif matched_file_or_class:
        anchor = "file_class"
    else:
        anchor = "opaque"

    return {
        "anchor": anchor,
        "has_exact_method_anchor": anchor == "method",
        "has_file_or_class_anchor": bool(matched_file_or_class),
        "matched_methods": matched_methods,
        "matched_file_or_class": matched_file_or_class,
        "gold_methods_considered": sorted(ids["methods"]),
        "gold_file_stems_considered": sorted(ids["file_stems"]),
        "gold_classes_considered": sorted(ids["classes"]),
    }


def java_frames(text: str) -> list[dict[str, str | None]]:
    frames: list[dict[str, str | None]] = []
    for match in re.finditer(r"\bat ([\w$.<>]+)\(([\w]+\.java):\d+\)", text):
        fqcn, filename = match.group(1), match.group(2)
        if EXTERNAL_JAVA.match(fqcn):
            continue

        pkg_parts: list[str] = []
        for part in fqcn.split("."):
            if part and part[0].islower():
                pkg_parts.append(part)
            else:
                break
        frames.append(
            {
                "file": filename,
                "context": ".".join(pkg_parts) if pkg_parts else fqcn,
                "language": "java",
            }
        )
    return frames


def stack_frames(text: str) -> list[dict[str, str | None]]:
    frames: list[dict[str, str | None]] = []
    frames.extend(java_frames(text))

    for match in re.finditer(r"([\w/._-]+\.go):\d+", text):
        path = match.group(1)
        top = path.split("/")[0] if "/" in path else path
        if not EXTERNAL_GO.match(top):
            frames.append(
                {
                    "file": Path(path).name,
                    "context": str(Path(path).parent),
                    "language": "go",
                }
            )

    for match in re.finditer(r'File "([^"]+\.py)", line \d+', text):
        path = match.group(1)
        frames.append(
            {
                "file": Path(path).name,
                "context": str(Path(path).parent),
                "language": "python",
            }
        )

    for match in re.finditer(r"([\w/._-]+\.(?:cc|cpp|h)):\d+", text):
        path = match.group(1)
        frames.append(
            {
                "file": Path(path).name,
                "context": str(Path(path).parent),
                "language": "cpp",
            }
        )

    for match in re.finditer(r"([\w/._-]+\.rs):\d+", text):
        path = match.group(1)
        frames.append(
            {
                "file": Path(path).name,
                "context": str(Path(path).parent),
                "language": "rust",
            }
        )

    seen: set[str] = set()
    deduped: list[dict[str, str | None]] = []
    for frame in frames:
        filename = frame["file"]
        if filename and filename not in seen:
            seen.add(filename)
            deduped.append(frame)
    return deduped


def classify_crash_to_fix(
    report: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    frames = stack_frames(report_text(report))
    gt_files = unique_ordered([block["file"] for block in blocks if block.get("file")])
    gt_basenames = {Path(path).name for path in gt_files}

    if not frames:
        return {
            "distance": "no_stack_trace",
            "crash_site": None,
            "crash_context": None,
            "stack_frames": [],
            "gold_files": gt_files,
        }

    crash_site = frames[0]["file"]
    crash_context = frames[0]["context"]

    if crash_site in gt_basenames:
        distance = "co_located"
    elif crash_context:
        distance = "cross_component"
        normalized_crash_context = crash_context.lower().replace("/", ".").replace("\\", ".")
        for gt_file in gt_files:
            gt_dir = str(Path(gt_file).parent).lower().replace("/", ".").replace("\\", ".")
            if normalized_crash_context == gt_dir:
                distance = "adjacent"
                break
            crash_parts = normalized_crash_context.split(".")
            gt_parts = gt_dir.split(".")
            if len(crash_parts) >= 2 and len(gt_parts) >= 2 and crash_parts[-2:] == gt_parts[-2:]:
                distance = "adjacent"
                break
    else:
        distance = "cross_component"
        crash_stem = Path(crash_site or "").stem.lower()
        for gt_file in gt_files:
            gt_stem = Path(gt_file).stem.lower()
            gt_dir_parts = [part.lower() for part in Path(gt_file).parent.parts]
            if min(len(crash_stem), len(gt_stem)) >= 6 and crash_stem[:6] == gt_stem[:6]:
                distance = "adjacent"
                break
            if any(crash_stem in part or part in crash_stem for part in gt_dir_parts if len(part) > 4):
                distance = "adjacent"
                break

    return {
        "distance": distance,
        "crash_site": crash_site,
        "crash_context": crash_context,
        "stack_frames": frames,
        "gold_files": gt_files,
    }


def bug_dirs(benchmark_root: Path) -> list[tuple[str, str, Path]]:
    bugs_root = benchmark_root / "bugs"
    bugs: list[tuple[str, str, Path]] = []
    for system_dir in sorted(bugs_root.iterdir()):
        if not system_dir.is_dir():
            continue
        for bug_dir in sorted(system_dir.iterdir()):
            if bug_dir.is_dir():
                bugs.append((system_dir.name, bug_dir.name, bug_dir))
    return bugs


def characterize_all(benchmark_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for system, bug_id, bug_dir in bug_dirs(benchmark_root):
        report_path = bug_dir / "report.json"
        blocks_path = bug_dir / "blocks.json"
        if not report_path.is_file() or not blocks_path.is_file():
            continue

        report = load_json(report_path)
        blocks = load_json(blocks_path)
        name = f"{system}-{bug_id}"
        anchor = classify_anchor(report, blocks)
        crash_to_fix = classify_crash_to_fix(report, blocks)

        result[name] = {
            "name": name,
            "system": system,
            "bug_id": bug_id,
            "textual_anchor": anchor["anchor"],
            "has_exact_method_anchor": anchor["has_exact_method_anchor"],
            "has_file_or_class_anchor": anchor["has_file_or_class_anchor"],
            "matched_methods": anchor["matched_methods"],
            "matched_file_or_class": anchor["matched_file_or_class"],
            "gold_methods_considered": anchor["gold_methods_considered"],
            "gold_file_stems_considered": anchor["gold_file_stems_considered"],
            "gold_classes_considered": anchor["gold_classes_considered"],
            "crash_to_fix_distance": crash_to_fix["distance"],
            "crash_site": crash_to_fix["crash_site"],
            "crash_context": crash_to_fix["crash_context"],
            "stack_frames": crash_to_fix["stack_frames"],
            "gold_files": crash_to_fix["gold_files"],
            "root_cause_type": report.get("root_cause_type", ""),
            "contains_analysis": report.get("contains_analysis", ""),
            "num_gold_files": len({block["file"] for block in blocks if block.get("file")}),
        }
    return result


def pct(count: int, total: int) -> str:
    return f"{count} / {total} ({(100.0 * count / total if total else 0.0):.1f}%)"


def print_summary(characterizations: dict[str, dict[str, Any]]) -> None:
    total = len(characterizations)
    anchor_counts = Counter(entry["textual_anchor"] for entry in characterizations.values())
    distance_counts = Counter(entry["crash_to_fix_distance"] for entry in characterizations.values())

    print("Textual anchors")
    print("---------------")
    for anchor in ANCHORS:
        print(f"{anchor}: {pct(anchor_counts[anchor], total)}")
    print(f"no_exact_method: {pct(total - anchor_counts['method'], total)}")
    print()

    print("Crash-to-fix distance")
    print("---------------------")
    for distance in DISTANCES:
        print(f"{distance}: {pct(distance_counts[distance], total)}")
    print()

    print("Anchor x distance")
    print("-----------------")
    col_width = 18
    header = f"{'':15}" + "".join(f"{distance:>{col_width}}" for distance in DISTANCES) + f"{'TOTAL':>8}"
    print(header)
    for anchor in ANCHORS:
        row = f"{anchor:<15}"
        row_total = 0
        for distance in DISTANCES:
            count = sum(
                1
                for entry in characterizations.values()
                if entry["textual_anchor"] == anchor and entry["crash_to_fix_distance"] == distance
            )
            row += f"{count:>{col_width}}"
            row_total += count
        print(row + f"{row_total:>8}")
    total_row = f"{'TOTAL':<15}"
    for distance in DISTANCES:
        total_row += f"{distance_counts[distance]:>{col_width}}"
    print(total_row + f"{total:>8}")


def build_parser() -> argparse.ArgumentParser:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Characterize benchmark bug difficulty.")
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=repo_root / "benchmark",
        help="Path to the benchmark directory.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Optional JSON output path.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the JSON characterizations to stdout instead of a summary.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    characterizations = characterize_all(args.benchmark_root)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(characterizations, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(characterizations, indent=2, sort_keys=True))
    else:
        print_summary(characterizations)
        if args.output:
            print()
            print(f"Wrote {len(characterizations)} characterizations to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

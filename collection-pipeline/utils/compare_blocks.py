#!/usr/bin/env python3
"""
Load tempt_blocks.csv and blocks.csv into issue-keyed dicts and check whether
each benchmark (blocks.csv) function for an issue is contained in the
extracted blocks (tempt_blocks.csv) for that issue.
"""

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import os
from dotenv import load_dotenv

ENV = load_dotenv()
# Paths relative to project root
OUT_PATH = os.getenv("OUT_PATH")
BENCHMARK_PATH = os.getenv("BENCHMARK_PATH")
TEMPT_BLOCKS_CSV = Path(os.path.join(OUT_PATH, "issues", "zookeeper_blocks.csv"))
OUTPUT_BLOCKS_CSV = Path(os.path.join(OUT_PATH, "issues", "blocks.csv"))
ETCD_TEMPT_CSV = Path(os.path.join(OUT_PATH, "issues", "etcd_blocks.csv"))
ROOT_BLOCKS_CSV = Path(os.path.join(BENCHMARK_PATH, "blocks.csv"))


def normalize_file_path(path: str, source: str) -> str:
    """Normalize file path for comparison."""
    path = (path or "").strip()
    # Strip common prefixes from tempt_blocks (ZooKeeper and etcd)
    for prefix in (
        "../zookeeper/",
        "zookeeper/",
        "/mnt/Ubuntu/zookeeper/",
        "./",
        "/mnt/Ubuntu/etcd/",
        "etcd/",
    ):
        if path.startswith(prefix):
            path = path[len(prefix) :]
    return path


def load_tempt_blocks(
    csv_path: Path,
    issue_id_prefix: Optional[str] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load tempt_blocks.csv into a dict: issue_id -> list of blocks.
    Each block: {file_path, start_line, end_line, class_func}.
    If issue_id_prefix is set (e.g. "ETCD-"), prepend it to each issue_id from the CSV.
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            issue_id = (row.get("issue_id") or "").strip()
            if not issue_id:
                continue
            # Only prepend prefix if the CSV id doesn't already have it (e.g. zookeeper_blocks has "ZOOKEEPER-2581", etcd_blocks has "13192")
            if issue_id_prefix and not issue_id.startswith(issue_id_prefix):
                issue_id = issue_id_prefix + issue_id
            file_path = normalize_file_path(row.get("file_path", ""), "tempt")
            start = row.get("start_line", "")
            end = row.get("end_line", "")
            try:
                start_line = int(start) if start else None
            except ValueError:
                start_line = None
            try:
                end_line = int(end) if end else None
            except ValueError:
                end_line = None
            class_func = (row.get("class_func") or "").strip() or None
            block = {
                "file_path": file_path,
                "start_line": start_line,
                "end_line": end_line,
                "class_func": class_func,
            }
            result.setdefault(issue_id, []).append(block)
    return result


def load_output_blocks(csv_path: Path) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load output/issues/blocks.csv (benchmark) into a dict: issue_id -> list of blocks.
    CSV columns: System, ID, Buggy File, Buggy Function, Start Line, End Line, Comment.
    issue_id = ZOOKEEPER-{ID}
    """
    result: Dict[str, List[Dict[str, Any]]] = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            system = (row.get("System") or "").strip()
            id_val = (row.get("ID") or "").strip()
            if not id_val:
                continue
            # Map System to issue prefix (ZooKeeper -> ZOOKEEPER, etcd -> ETCD, etc.)
            prefix = (
                "ZOOKEEPER"
                if system.lower() == "zookeeper"
                else system.upper().replace(" ", "")
            )
            issue_id = f"{prefix}-{id_val}"
            buggy_file = normalize_file_path(row.get("Buggy File", ""), "output")
            func = (row.get("Buggy Function") or "").strip() or None
            start = row.get("Start Line", "")
            end = row.get("End Line", "")
            try:
                start_line = int(start) if start else None
            except ValueError:
                start_line = None
            try:
                end_line = int(end) if end else None
            except ValueError:
                end_line = None
            block = {
                "file_path": buggy_file,
                "buggy_function": func,
                "start_line": start_line,
                "end_line": end_line,
                "comment": (row.get("Comment") or "").strip(),
            }
            result.setdefault(issue_id, []).append(block)
    return result


def same_file(a: str, b: str) -> bool:
    """Compare paths after normalizing (both should already be normalized)."""
    a = (a or "").replace("\\", "/").strip("/")
    b = (b or "").replace("\\", "/").strip("/")
    if a == b:
        return True
    # One may have different prefix (e.g. src/... vs zookeeper-server/...); compare by filename and last segment
    if a.split("/")[-1] != b.split("/")[-1]:
        return False
    # Same filename: require path suffix to match (e.g. .../org/apache/zookeeper/.../File.java)
    a_parts = a.split("/")
    b_parts = b.split("/")
    for n in range(1, min(len(a_parts), len(b_parts)) + 1):
        if a_parts[-n:] == b_parts[-n:]:
            return True
    return False


def function_matches(tempt_func: Optional[str], benchmark_func: Optional[str]) -> bool:
    """Check if benchmark function is contained in tempt (exact or suffix match)."""
    if benchmark_func is None:
        return True
    if tempt_func is None:
        return False
    if tempt_func == benchmark_func:
        return True
    # Tempt might be more specific (e.g. inner class): Leader.getEpochToPropose vs Leader.Leader.getEpochToPropose
    if benchmark_func in tempt_func:
        return True
    # Same method name at end (e.g. getEpochToPropose)
    if benchmark_func.split(".")[-1] == tempt_func.split(".")[-1]:
        return True
    return False


def line_range_overlap(
    a_start: Optional[int],
    a_end: Optional[int],
    b_start: Optional[int],
    b_end: Optional[int],
) -> bool:
    """True if [a_start, a_end] and [b_start, b_end] overlap or b is inside a."""
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return True  # skip line check if missing
    return not (b_end < a_start or b_start > a_end)


def line_overlap_length(
    t_start: Optional[int],
    t_end: Optional[int],
    b_start: Optional[int],
    b_end: Optional[int],
) -> int:
    """Number of lines in the intersection of [t_start, t_end] and [b_start, b_end]."""
    if t_start is None or t_end is None or b_start is None or b_end is None:
        return 0
    overlap_start = max(t_start, b_start)
    overlap_end = min(t_end, b_end)
    return max(0, overlap_end - overlap_start + 1)


def is_benchmark_block_function_covered(
    benchmark: dict[str, Any],
    tempt_blocks: list[dict[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """
    Return (covered, match_description).
    Function-covered if same file and at least one tempt block has matching function
    (ground truth block counts as covered by us if we identified the correct function).
    No line overlap required.
    """
    b_file = benchmark.get("file_path") or ""
    b_func = benchmark.get("buggy_function")
    for t in tempt_blocks:
        t_file = t.get("file_path") or ""
        if not same_file(t_file, b_file):
            continue
        if not function_matches(t.get("class_func"), b_func):
            continue
        return (
            True,
            f"file={t_file!r} func={t.get('class_func')!r}",
        )
    return False, None


def is_benchmark_block_contained_in_tempt(
    benchmark: dict[str, Any],
    tempt_blocks: list[dict[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """
    Return (contained, match_description).
    Contained if same issue has at least one tempt block with same file, same function, and overlapping line range.
    """
    b_file = benchmark.get("file_path") or ""
    b_func = benchmark.get("buggy_function")
    b_start = benchmark.get("start_line")
    b_end = benchmark.get("end_line")
    for t in tempt_blocks:
        t_file = t.get("file_path") or ""
        if not same_file(t_file, b_file):
            continue
        if not function_matches(t.get("class_func"), b_func):
            continue
        if line_range_overlap(
            t.get("start_line"),
            t.get("end_line"),
            b_start,
            b_end,
        ):
            return (
                True,
                f"file={t_file!r} func={t.get('class_func')!r} lines={t.get('start_line')}-{t.get('end_line')}",
            )
    return False, None


def get_unique_files_from_blocks(
    blocks: List[Dict[str, Any]],
    file_key: str = "file_path",
) -> List[str]:
    """Get unique files from blocks, treating same_file() as equivalence."""
    seen: List[str] = []
    for b in blocks:
        fp = (b.get(file_key) or "").strip()
        if not fp:
            continue
        if any(same_file(fp, s) for s in seen):
            continue
        seen.append(fp)
    return seen


def file_precision_for_issue(
    tempt_blocks: List[Dict[str, Any]],
    benchmark_blocks: List[Dict[str, Any]],
) -> Tuple[float, int, int]:
    """
    Return (file_precision, matched_file_count, total_tempt_files).
    File precision: fraction of unique extracted files that contain at least one benchmark block.
    """
    tempt_files = get_unique_files_from_blocks(tempt_blocks)
    if not tempt_files or not benchmark_blocks:
        return 0.0, 0, len(tempt_files)
    matched = sum(
        1
        for tf in tempt_files
        if any(
            same_file(tf, (b.get("file_path") or "").strip())
            for b in benchmark_blocks
        )
    )
    prec = matched / len(tempt_files) if tempt_files else 0.0
    return prec, matched, len(tempt_files)


def is_tempt_block_matched_by_benchmark(
    tempt_block: Dict[str, Any],
    benchmark_blocks: List[Dict[str, Any]],
) -> bool:
    """
    Return True if this tempt (extracted) block overlaps with at least one benchmark block.
    Used for precision: what fraction of extracted blocks are relevant?
    """
    matched, _ = matched_block_line_precision(tempt_block, benchmark_blocks)
    return matched


def matched_block_line_precision(
    tempt_block: Dict[str, Any],
    benchmark_blocks: List[Dict[str, Any]],
) -> Tuple[bool, float]:
    """
    For a tempt block and list of benchmark blocks, return (matched, line_precision).
    matched: True if at least one benchmark block overlaps (same file, function, line overlap).
    line_precision: when matched, (overlap lines) / (tempt block lines), using the benchmark
    that maximizes overlap. So 1.0 = extracted range is entirely inside a benchmark range.
    """
    t_file = tempt_block.get("file_path") or ""
    t_func = tempt_block.get("class_func")
    t_start = tempt_block.get("start_line")
    t_end = tempt_block.get("end_line")
    tempt_lines = 0
    if t_start is not None and t_end is not None and t_end >= t_start:
        tempt_lines = t_end - t_start + 1
    best_overlap = 0
    for b in benchmark_blocks:
        b_file = b.get("file_path") or ""
        if not same_file(t_file, b_file):
            continue
        if not function_matches(t_func, b.get("buggy_function")):
            continue
        if not line_range_overlap(
            t_start,
            t_end,
            b.get("start_line"),
            b.get("end_line"),
        ):
            continue
        overlap = line_overlap_length(
            t_start,
            t_end,
            b.get("start_line"),
            b.get("end_line"),
        )
        if overlap > best_overlap:
            best_overlap = overlap
    if best_overlap == 0:
        return False, 0.0
    line_prec = (best_overlap / tempt_lines) if tempt_lines > 0 else 0.0
    return True, line_prec


# Comparison config: (label, tempt_csv_path, benchmark_csv_path, optional issue_id_prefix for tempt)
COMPARISONS = [
    ("ZooKeeper", TEMPT_BLOCKS_CSV, ROOT_BLOCKS_CSV, "ZOOKEEPER-"),
    ("etcd", ETCD_TEMPT_CSV, ROOT_BLOCKS_CSV, "ETCD-"),
]


def run_comparison(
    name: str,
    tempt_path: Path,
    benchmark_path: Path,
    issue_id_prefix: Optional[str],
) -> None:
    """Run full comparison for one (tempt, benchmark) pair."""
    if not tempt_path.exists():
        print(f"[{name}] Missing: {tempt_path}")
        return
    if not benchmark_path.exists():
        print(f"[{name}] Missing: {benchmark_path}")
        return

    # tempt_by_issue = load_tempt_blocks(tempt_path, issue_id_prefix=issue_id_prefix)
    tempt_by_issue = {}
    for file in os.listdir("output/agent/zookeeper/chat"):
        with open(os.path.join("output/agent/zookeeper/chat", file), "r") as f:
            try:
                data = json.load(f)
                tempt_by_issue[file.split(".")[0]] = data["code_blocks"]
            except Exception as e:
                print(f"Error loading {file}: {e}")
                continue
    benchmark_by_issue = load_output_blocks(benchmark_path)

    # Restrict to issues that belong to this comparison (e.g. only ETCD-* for etcd)
    all_issues = set(tempt_by_issue) | set(benchmark_by_issue)
    if issue_id_prefix:
        all_issues = {i for i in all_issues if i.startswith(issue_id_prefix)}
    all_issues = sorted(all_issues)

    if not all_issues:
        print(
            f"[{name}] No issues to compare (tempt: {tempt_path}, benchmark: {benchmark_path})"
        )
        return

    print(f"\n{'='*60}")
    print(f"  {name}: {tempt_path.name} vs {benchmark_path.name}")
    print("=" * 60)

    print("\n=== Tempt blocks (extracted) summary ===")
    for issue_id in all_issues:
        blocks = tempt_by_issue.get(issue_id, [])
        if not blocks:
            continue
        files = {b["file_path"] for b in blocks}
        funcs = [b["class_func"] for b in blocks if b.get("class_func")]
        print(
            f"  {issue_id}: {len(blocks)} blocks, {len(files)} files, {len(funcs)} with function"
        )

    print("\n=== Benchmark blocks (ground truth) summary ===")
    for issue_id in all_issues:
        blocks = benchmark_by_issue.get(issue_id, [])
        if not blocks:
            continue
        files = {b["file_path"] for b in blocks}
        funcs = [b["buggy_function"] for b in blocks if b.get("buggy_function")]
        print(
            f"  {issue_id}: {len(blocks)} blocks, {len(files)} files, {len(funcs)} with function"
        )

    # Per-issue: ours vs ground truth side-by-side
    print("\n=== Per-issue comparison (ours vs ground truth) ===")
    for issue_id in all_issues:
        tempt_blocks = tempt_by_issue.get(issue_id, [])
        benchmark_blocks = benchmark_by_issue.get(issue_id, [])
        print(f"\n--- {issue_id} ---")
        print("  Ours (extracted):")
        if not tempt_blocks:
            print("    (none)")
        else:
            for i, t in enumerate(tempt_blocks, 1):
                path = (t.get("file_path") or "").strip()
                s, e = t.get("start_line"), t.get("end_line")
                line_str = f"{s}-{e}" if s is not None and e is not None else "?-?"
                func = t.get("class_func") or "(no func)"
                matched, line_prec = (
                    matched_block_line_precision(t, benchmark_blocks)
                    if benchmark_blocks
                    else (False, 0.0)
                )
                status = (
                    f"  -> matches GT (line prec={line_prec:.0%})"
                    if matched
                    else "  -> no GT match"
                )
                print(f"    [{i}] {path}  lines {line_str}  func={func}{status}")
        print("  Ground truth:")
        if not benchmark_blocks:
            print("    (none)")
        else:
            for i, b in enumerate(benchmark_blocks, 1):
                path = (b.get("file_path") or "").strip()
                s, e = b.get("start_line"), b.get("end_line")
                line_str = f"{s}-{e}" if s is not None and e is not None else "?-?"
                func = b.get("buggy_function") or "(no func)"
                contained, _ = is_benchmark_block_contained_in_tempt(b, tempt_blocks)
                func_covered, _ = is_benchmark_block_function_covered(b, tempt_blocks)
                c = "contained" if contained else "not contained"
                f = "func covered" if func_covered else "func not covered"
                print(f"    [{i}] {path}  lines {line_str}  func={func}")
                print(f"        -> {c}, {f}")

    print("\n=== Containment: does tempt_blocks contain each benchmark block? ===")
    contained_count = 0
    missing_count = 0
    missing_list = []

    for issue_id in all_issues:
        tempt_blocks = tempt_by_issue.get(issue_id, [])
        benchmark_blocks = benchmark_by_issue.get(issue_id, [])
        if not benchmark_blocks:
            continue
        for b in benchmark_blocks:
            contained, _ = is_benchmark_block_contained_in_tempt(b, tempt_blocks)
            if contained:
                contained_count += 1
            else:
                missing_count += 1
                missing_list.append((issue_id, b))

    total_bench = sum(len(benchmark_by_issue.get(i, [])) for i in all_issues)
    print(f"  Benchmark blocks total: {total_bench}")
    print(f"  Contained in tempt_blocks: {contained_count}")
    print(f"  NOT contained (missing): {missing_count}")

    if missing_list:
        print("\n  Missing (benchmark block not found in tempt_blocks):")
        for issue_id, b in missing_list:
            print(
                f"    {issue_id}: file={b.get('file_path')!r} func={b.get('buggy_function')!r} lines={b.get('start_line')}-{b.get('end_line')}"
            )

    print(
        "\n=== Function coverage (ground-truth blocks with correct function identified) ==="
    )
    func_covered_count = 0
    func_not_covered_count = 0
    func_not_covered_list: List[Tuple[str, Dict[str, Any]]] = []
    for issue_id in all_issues:
        tempt_blocks = tempt_by_issue.get(issue_id, [])
        benchmark_blocks = benchmark_by_issue.get(issue_id, [])
        if not benchmark_blocks:
            continue
        for b in benchmark_blocks:
            covered, _ = is_benchmark_block_function_covered(b, tempt_blocks)
            if covered:
                func_covered_count += 1
            else:
                func_not_covered_count += 1
                func_not_covered_list.append((issue_id, b))
    print(f"  Benchmark (ground-truth) blocks total: {total_bench}")
    print(f"  Function covered (correct function given): {func_covered_count}")
    print(f"  Function NOT covered: {func_not_covered_count}")
    if total_bench > 0:
        func_coverage_pct = 100.0 * func_covered_count / total_bench
        print(
            f"  Function coverage: {func_covered_count}/{total_bench} = {func_coverage_pct:.1f}%"
        )
    if func_not_covered_list:
        print("\n  Not function-covered (we did not identify the correct function):")
        for issue_id, b in func_not_covered_list:
            print(
                f"    {issue_id}: file={b.get('file_path')!r} func={b.get('buggy_function')!r} lines={b.get('start_line')}-{b.get('end_line')}"
            )

    print("\n=== Precision (extracted blocks that match a benchmark block) ===")
    total_tempt_matched = 0
    total_tempt_count = 0
    precision_by_issue: List[Tuple[str, float, int, int]] = []
    line_precisions_matched: List[float] = []
    line_precision_by_issue: List[Tuple[str, float, int]] = []

    for issue_id in all_issues:
        tempt_blocks = tempt_by_issue.get(issue_id, [])
        benchmark_blocks = benchmark_by_issue.get(issue_id, [])
        if not benchmark_blocks or not tempt_blocks:
            continue
        matched_count = 0
        issue_line_precs: List[float] = []
        for t in tempt_blocks:
            matched, line_prec = matched_block_line_precision(t, benchmark_blocks)
            if matched:
                matched_count += 1
                issue_line_precs.append(line_prec)
                line_precisions_matched.append(line_prec)
        total_tempt_matched += matched_count
        total_tempt_count += len(tempt_blocks)
        prec = matched_count / len(tempt_blocks) if tempt_blocks else 0.0
        precision_by_issue.append((issue_id, prec, matched_count, len(tempt_blocks)))
        avg_line_prec = (
            sum(issue_line_precs) / len(issue_line_precs) if issue_line_precs else 0.0
        )
        line_precision_by_issue.append((issue_id, avg_line_prec, matched_count))

    for issue_id, prec, matched, total in precision_by_issue:
        print(f"  {issue_id}: precision = {matched}/{total} = {prec:.2%}")

    if total_tempt_count > 0:
        overall_precision = total_tempt_matched / total_tempt_count
        print(
            f"\n  Overall precision (micro): {total_tempt_matched}/{total_tempt_count} = {overall_precision:.2%}"
        )
    if precision_by_issue:
        macro_precision = sum(p[1] for p in precision_by_issue) / len(
            precision_by_issue
        )
        print(f"  Macro precision (avg over issues): {macro_precision:.2%}")

    print("\n=== File precision (extracted files that contain a benchmark block) ===")
    total_tempt_files_matched = 0
    total_tempt_files = 0
    file_precision_by_issue: List[Tuple[str, float, int, int]] = []
    for issue_id in all_issues:
        tempt_blocks = tempt_by_issue.get(issue_id, [])
        benchmark_blocks = benchmark_by_issue.get(issue_id, [])
        if not benchmark_blocks or not tempt_blocks:
            continue
        fp, matched_files, total_files = file_precision_for_issue(
            tempt_blocks, benchmark_blocks
        )
        total_tempt_files_matched += matched_files
        total_tempt_files += total_files
        file_precision_by_issue.append((issue_id, fp, matched_files, total_files))
    for issue_id, fp, matched_files, total_files in file_precision_by_issue:
        print(f"  {issue_id}: file precision = {matched_files}/{total_files} = {fp:.2%}")
    if total_tempt_files > 0:
        overall_file_precision = total_tempt_files_matched / total_tempt_files
        print(
            f"\n  Overall file precision (micro): {total_tempt_files_matched}/{total_tempt_files} = {overall_file_precision:.2%}"
        )
    if file_precision_by_issue:
        macro_file_precision = sum(p[1] for p in file_precision_by_issue) / len(
            file_precision_by_issue
        )
        print(f"  Macro file precision (avg over issues): {macro_file_precision:.2%}")

    print(
        "\n=== Line precision (per matched block: overlap lines / extracted block lines) ==="
    )
    for issue_id, avg_line_prec, num_matched in line_precision_by_issue:
        if num_matched > 0:
            print(
                f"  {issue_id}: avg line precision = {avg_line_prec:.2%} (over {num_matched} matched blocks)"
            )
    if line_precisions_matched:
        overall_line_prec = sum(line_precisions_matched) / len(line_precisions_matched)
        print(
            f"\n  Overall line precision (micro, over matched blocks): {overall_line_prec:.2%} ({len(line_precisions_matched)} blocks)"
        )
    if line_precision_by_issue:
        issues_with_matched = [
            (i, p, n) for i, p, n in line_precision_by_issue if n > 0
        ]
        if issues_with_matched:
            macro_line_prec = sum(p for _, p, n in issues_with_matched) / len(
                issues_with_matched
            )
            print(
                f"  Macro line precision (avg of per-issue avg): {macro_line_prec:.2%}"
            )


def main():
    for name, tempt_path, benchmark_path, prefix in COMPARISONS:
        run_comparison(name, tempt_path, benchmark_path, prefix)


def main2():
    tempt_by_issue = load_tempt_blocks(TEMPT_BLOCKS_CSV, issue_id_prefix="ZOOKEEPER-")
    benchmark_by_issue = load_output_blocks(OUTPUT_BLOCKS_CSV)
    # print(json.dumps(benchmark_by_issue, indent=2))
    blocks = {}
    for file in os.listdir("output/agent/zookeeper/chat"):
        with open(os.path.join("output/agent/zookeeper/chat", file), "r") as f:
            try:
                data = json.load(f)
                blocks[file.split(".")[0]] = data["code_blocks"]
            except Exception as e:
                print(f"Error loading {file}: {e}")
                continue
    print(json.dumps(blocks, indent=2))


if __name__ == "__main__":
    main()

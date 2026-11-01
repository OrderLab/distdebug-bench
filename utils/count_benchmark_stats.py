from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _format_counter(counter: Counter[str]) -> list[str]:
    total = sum(counter.values())
    lines: list[str] = []
    for key, value in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
        pct = 100.0 * value / total if total else 0.0
        lines.append(f"  - {key}: {value} ({pct:.1f}%)")
    return lines


def _pct(value: int, total: int) -> str:
    pct = 100.0 * value / total if total else 0.0
    return f"{value} / {total} ({pct:.1f}%)"


def _format_summary(values: list[float]) -> str:
    if not values:
        return "n/a"
    return (
        f"mean={mean(values):.2f}, median={median(values):.2f}, "
        f"min={min(values):.2f}, max={max(values):.2f}"
    )


def _system_from_bug_name(name: str) -> str:
    return name.split("-", 1)[0]


def _print_section(title: str) -> None:
    print(title)
    print("-" * len(title))


def _summarize_release(
    bug_dirs: list[tuple[str, str]],
    full_classifications: dict[str, dict],
    difficulty_characterizations: dict[str, dict],
    benchmark_root: Path,
) -> None:
    system_counts = Counter(system for system, _ in bug_dirs)
    bug_names = [f"{system}-{bug_id}" for system, bug_id in bug_dirs]

    classified_names = set(full_classifications.keys())
    missing_classification = sorted(set(bug_names) - classified_names)
    extra_classification = sorted(classified_names - set(bug_names))

    _print_section("Full benchmark release")
    print(f"Total bugs: {len(bug_names)}")
    print(f"Total systems: {len(system_counts)}")
    print("Per-system breakdown:")
    print("\n".join(_format_counter(system_counts)))
    print()

    bool_fields = [
        ("has_logs", "Has logs"),
        ("has_stack_traces", "Has stack traces"),
        ("has_analysis", "Has prior analysis"),
    ]
    print("Issue-signal metadata:")
    for field, label in bool_fields:
        positive = sum(1 for name in bug_names if full_classifications.get(name, {}).get(field))
        print(f"  - {label}: {_pct(positive, len(bug_names))}")

    derived_bool_fields = [
        (
            "Has artifacts",
            lambda entry: (entry.get("n_artifacts") or 0) > 0,
        ),
        (
            "Has past issue references",
            lambda entry: bool(entry.get("jira_refs") or entry.get("gh_issue_refs")),
        ),
        (
            "Has causal phrases",
            lambda entry: (entry.get("n_causal_refs") or entry.get("causal_phrases") or 0) > 0,
        ),
        (
            "Has code identifiers",
            lambda entry: (entry.get("n_code_identifiers") or 0) > 0,
        ),
    ]
    for label, predicate in derived_bool_fields:
        positive = sum(
            1
            for name in bug_names
            if name in full_classifications and predicate(full_classifications[name])
        )
        print(f"  - {label}: {_pct(positive, len(bug_names))}")

    numeric_fields = [
        ("desc_length", "Description length"),
        ("prose_length", "Prose length"),
        ("n_artifacts", "# artifacts"),
        ("description_richness", "Description richness"),
        ("n_causal_refs", "# causal phrase hits"),
        ("n_code_identifiers", "# code identifiers"),
    ]
    for field, label in numeric_fields:
        values = [
            float(full_classifications[name][field])
            for name in bug_names
            if name in full_classifications and full_classifications[name].get(field) is not None
        ]
        if values:
            print(f"  - {label}: {_format_summary(values)}")
    print()

    _summarize_ground_truth(benchmark_root, bug_dirs)
    print()

    if difficulty_characterizations:
        _summarize_difficulty_characterizations(bug_names, difficulty_characterizations)
        print()

    if missing_classification or extra_classification:
        print("Consistency checks:")
        print(f"  - Missing classification entries: {len(missing_classification)}")
        if missing_classification:
            print(f"    {', '.join(missing_classification[:10])}")
        print(f"  - Extra classification entries: {len(extra_classification)}")
        if extra_classification:
            print(f"    {', '.join(extra_classification[:10])}")
        print()


def _summarize_difficulty_characterizations(
    bug_names: list[str],
    difficulty_characterizations: dict[str, dict],
) -> None:
    _print_section("Ground-truth-aware difficulty")
    total = len(bug_names)
    known_names = [name for name in bug_names if name in difficulty_characterizations]

    anchor_counts = Counter(
        difficulty_characterizations[name].get("textual_anchor", "Unknown")
        for name in known_names
    )
    distance_counts = Counter(
        difficulty_characterizations[name].get("crash_to_fix_distance", "Unknown")
        for name in known_names
    )

    print("Textual anchors:")
    for anchor in ["method", "file_class", "opaque"]:
        print(f"  - {anchor}: {_pct(anchor_counts[anchor], total)}")
    print(f"  - no exact method: {_pct(total - anchor_counts['method'], total)}")

    print("Crash-to-fix distance:")
    for distance in ["no_stack_trace", "cross_component", "co_located", "adjacent"]:
        print(f"  - {distance}: {_pct(distance_counts[distance], total)}")

    missing = sorted(set(bug_names) - set(difficulty_characterizations))
    extra = sorted(set(difficulty_characterizations) - set(bug_names))
    if missing or extra:
        print("Difficulty characterization consistency checks:")
        print(f"  - Missing entries: {len(missing)}")
        if missing:
            print(f"    {', '.join(missing[:10])}")
        print(f"  - Extra entries: {len(extra)}")
        if extra:
            print(f"    {', '.join(extra[:10])}")


def _summarize_ground_truth(
    benchmark_root: Path,
    bug_dirs: list[tuple[str, str]],
) -> None:
    block_counts: list[int] = []
    file_counts: list[int] = []
    block_spans: list[int] = []
    total_blocks = 0
    multi_block = 0
    cross_file = 0
    same_file_multi = 0
    singleton_line_blocks = 0
    missing_function = 0

    for system, bug_id in bug_dirs:
        blocks_path = benchmark_root / "bugs" / system / bug_id / "blocks.json"
        if not blocks_path.is_file():
            continue
        blocks = _load_json(blocks_path)
        if not isinstance(blocks, list):
            continue

        files = {
            block.get("file")
            for block in blocks
            if isinstance(block, dict) and block.get("file")
        }
        block_counts.append(len(blocks))
        file_counts.append(len(files))
        total_blocks += len(blocks)
        if len(blocks) > 1:
            multi_block += 1
        if len(files) > 1:
            cross_file += 1
        if len(blocks) > 1 and len(files) == 1:
            same_file_multi += 1

        for block in blocks:
            if not isinstance(block, dict):
                continue
            if not block.get("function"):
                missing_function += 1
            try:
                start = int(block.get("start_line"))
                end = int(block.get("end_line"))
            except (TypeError, ValueError):
                continue
            span = max(1, end - start + 1)
            block_spans.append(span)
            if start == end:
                singleton_line_blocks += 1

    _print_section("Ground-truth label structure")
    print(f"Total labeled bugs: {len(block_counts)}")
    print(f"Total buggy blocks: {total_blocks}")
    print(f"Blocks per bug: {_format_summary([float(v) for v in block_counts])}")
    print(f"Files per bug: {_format_summary([float(v) for v in file_counts])}")
    print(f"Lines per block: {_format_summary([float(v) for v in block_spans])}")
    print(f"Multi-block bugs: {_pct(multi_block, len(block_counts))}")
    print(f"Same-file multi-block bugs: {_pct(same_file_multi, len(block_counts))}")
    print(f"Cross-file bugs: {_pct(cross_file, len(block_counts))}")
    print(f"Single-line blocks: {_pct(singleton_line_blocks, total_blocks)}")
    print(f"Blocks missing function labels: {_pct(missing_function, total_blocks)}")


def _summarize_subset(
    subset_bug_names: list[str],
    subset_labels: dict[str, dict],
) -> None:
    system_counts = Counter(_system_from_bug_name(name) for name in subset_bug_names)

    _print_section("Filtered evaluation subset")
    print(f"Total bugs: {len(subset_bug_names)}")
    print(f"Total systems: {len(system_counts)}")
    print("Per-system breakdown:")
    print("\n".join(_format_counter(system_counts)))
    print()

    missing_labels = sorted(set(subset_bug_names) - set(subset_labels.keys()))
    extra_labels = sorted(set(subset_labels.keys()) - set(subset_bug_names))
    print("Label distributions:")
    for field, label in [
        ("severity", "Severity"),
        ("root_cause_type", "Root cause type"),
        ("bug_complexity", "Bug complexity"),
        ("distributed", "Distributed"),
    ]:
        counter = Counter(
            subset_labels[name].get(field, "Unknown")
            for name in subset_bug_names
            if name in subset_labels
        )
        print(f"{label}:")
        print("\n".join(_format_counter(counter)))
        print()

    if missing_labels or extra_labels:
        print("Consistency checks:")
        print(f"  - Missing label entries: {len(missing_labels)}")
        if missing_labels:
            print(f"    {', '.join(missing_labels[:10])}")
        print(f"  - Extra label entries: {len(extra_labels)}")
        if extra_labels:
            print(f"    {', '.join(extra_labels[:10])}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize DistDebug-Bench release composition and label metadata."
    )
    parser.add_argument(
        "--include-subset",
        action="store_true",
        help="Also print the legacy filtered evaluation subset summary.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    benchmark_root = repo_root / "benchmark"
    bugs_root = benchmark_root / "bugs"

    full_classifications = _load_json(benchmark_root / "bug_classifications.json")
    difficulty_path = benchmark_root / "bug_difficulty_characterizations.json"
    difficulty_characterizations = _load_json(difficulty_path) if difficulty_path.is_file() else {}

    bug_dirs: list[tuple[str, str]] = []
    for system_dir in sorted(bugs_root.iterdir()):
        if not system_dir.is_dir():
            continue
        for bug_dir in sorted(system_dir.iterdir()):
            if bug_dir.is_dir():
                bug_dirs.append((system_dir.name, bug_dir.name))

    _summarize_release(
        bug_dirs,
        full_classifications,
        difficulty_characterizations,
        benchmark_root,
    )
    if args.include_subset:
        subset_labels = _load_json(repo_root / "filtered_benchmark_labels.json")
        subset_bug_names = [
            line.strip()
            for line in (repo_root / "filtered_bug_list.txt").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        _summarize_subset(subset_bug_names, subset_labels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

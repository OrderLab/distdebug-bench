#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean, median

import matplotlib.pyplot as plt
import numpy as np


BAR_HEIGHT = 0.58
ROW_STEP = BAR_HEIGHT
BAR_Y_TOP_PAD = 0.12
BAR_Y_BOTTOM_PAD = 0.12
BAR_BG = "#edf0f3"
SUMMARY_HEADER_GAP = 0.105
SUMMARY_ROW_STEP = 0.096
BAR_AXES_HEIGHT_SCALE = 0.82


SYSTEM_LABELS = {
    "elasticsearch": "Elasticsearch",
    "etcd": "etcd",
    "hbase": "HBase",
    "hdfs": "HDFS",
    "ignite": "Ignite",
    "ignite3": "Ignite 3",
    "kafka": "Kafka",
    "kubernetes": "Kubernetes",
    "ray": "Ray",
    "tikv": "TiKV",
    "zookeeper": "ZooKeeper",
}

GENERIC_METHODS = {
    "add",
    "apply",
    "build",
    "call",
    "check",
    "close",
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


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def pct(count: int, total: int) -> float:
    return 100.0 * count / total if total else 0.0


def count_pct_short(count: int, total: int) -> str:
    return f"{count} ({pct(count, total):.0f}%)"


def report_text(report: dict) -> str:
    return f"{report.get('title', '')}\n{report.get('description', '')}".strip()


def gt_ids(blocks: list[dict]) -> dict[str, set[str]]:
    methods: set[str] = set()
    file_stems: set[str] = set()
    classes: set[str] = set()
    for block in blocks:
        stem = Path(block.get("file", "")).stem
        if len(stem) > 3:
            file_stems.add(stem)
        function = block.get("function", "")
        if function:
            parts = re.split(r"\.|::", function)
            leaf = re.sub(r"<[^>]*>", "", parts[-1]).strip()
            if len(leaf) > 5 and leaf not in GENERIC_METHODS:
                methods.add(leaf)
            if len(parts) > 1 and len(parts[-2]) > 3:
                classes.add(parts[-2])
    return {"methods": methods, "file_stems": file_stems, "classes": classes}


def has_exact_method_anchor(report: dict, blocks: list[dict]) -> bool:
    text = report_text(report)
    return any(method in text for method in gt_ids(blocks)["methods"])


def java_frames(text: str) -> list[tuple[str, str]]:
    frames: list[tuple[str, str]] = []
    for match in re.finditer(r"\bat ([\w$.<>]+)\(([\w]+\.java):\d+\)", text):
        fqcn, filename = match.group(1), match.group(2)
        if not EXTERNAL_JAVA.match(fqcn):
            pkg_parts: list[str] = []
            for part in fqcn.split("."):
                if part and part[0].islower():
                    pkg_parts.append(part)
                else:
                    break
            frames.append((filename, ".".join(pkg_parts) if pkg_parts else fqcn))
    return frames


def stack_files(text: str) -> list[tuple[str, str | None]]:
    frames: list[tuple[str, str | None]] = []
    frames.extend(java_frames(text))
    for match in re.finditer(r"([\w/._-]+\.go):\d+", text):
        path = match.group(1)
        top = path.split("/")[0] if "/" in path else path
        if not EXTERNAL_GO.match(top):
            frames.append((Path(path).name, str(Path(path).parent)))
    for match in re.finditer(r'File "([^"]+\.py)", line \d+', text):
        path = match.group(1)
        frames.append((Path(path).name, str(Path(path).parent)))
    for match in re.finditer(r"([\w/._-]+\.(?:cc|cpp|h)):\d+", text):
        path = match.group(1)
        frames.append((Path(path).name, str(Path(path).parent)))
    for match in re.finditer(r"([\w/._-]+\.rs):\d+", text):
        path = match.group(1)
        frames.append((Path(path).name, str(Path(path).parent)))

    seen: set[str] = set()
    deduped: list[tuple[str, str | None]] = []
    for filename, context in frames:
        if filename not in seen:
            seen.add(filename)
            deduped.append((filename, context))
    return deduped


def classify_crash_distance(report: dict, blocks: list[dict]) -> str:
    frames = stack_files(report_text(report))
    if not frames:
        return "no_stack_trace"

    gt_basenames = {Path(block["file"]).name for block in blocks}
    gt_files = [block["file"] for block in blocks]
    crash_site, crash_context = frames[0]

    if crash_site in gt_basenames:
        return "co_located"

    if crash_context:
        crash_context = crash_context.lower().replace("/", ".").replace("\\", ".")
        for gt_file in gt_files:
            gt_dir = str(Path(gt_file).parent).lower().replace("/", ".").replace("\\", ".")
            if crash_context == gt_dir:
                return "adjacent"
            crash_parts = crash_context.split(".")
            gt_parts = gt_dir.split(".")
            if len(crash_parts) >= 2 and len(gt_parts) >= 2 and crash_parts[-2:] == gt_parts[-2:]:
                return "adjacent"
        return "cross_component"

    crash_stem = Path(crash_site).stem.lower()
    for gt_file in gt_files:
        gt_stem = Path(gt_file).stem.lower()
        gt_dir_parts = [part.lower() for part in Path(gt_file).parent.parts]
        if min(len(crash_stem), len(gt_stem)) >= 6 and crash_stem[:6] == gt_stem[:6]:
            return "adjacent"
        if any(crash_stem in part or part in crash_stem for part in gt_dir_parts if len(part) > 4):
            return "adjacent"
    return "cross_component"


def bug_dirs(benchmark_root: Path) -> list[tuple[str, str]]:
    bugs_root = benchmark_root / "bugs"
    dirs: list[tuple[str, str]] = []
    for system_dir in sorted(bugs_root.iterdir()):
        if not system_dir.is_dir():
            continue
        for bug_dir in sorted(system_dir.iterdir()):
            if bug_dir.is_dir():
                dirs.append((system_dir.name, bug_dir.name))
    return dirs


def summarize_ground_truth(benchmark_root: Path, bugs: list[tuple[str, str]]) -> dict:
    block_counts: list[int] = []
    block_spans: list[int] = []
    multi_block = 0
    cross_file = 0
    total_blocks = 0

    for system, bug_id in bugs:
        blocks_path = benchmark_root / "bugs" / system / bug_id / "blocks.json"
        blocks = load_json(blocks_path)
        files = {block["file"] for block in blocks if block.get("file")}

        total_blocks += len(blocks)
        block_counts.append(len(blocks))
        if len(blocks) > 1:
            multi_block += 1
        if len(files) > 1:
            cross_file += 1

        for block in blocks:
            start = int(block["start_line"])
            end = int(block["end_line"])
            block_spans.append(max(1, end - start + 1))

    return {
        "total_blocks": total_blocks,
        "avg_blocks": mean(block_counts),
        "median_blocks": median(block_counts),
        "multi_block": multi_block,
        "cross_file": cross_file,
        "median_span": median(block_spans),
    }


def summarize_difficulty(benchmark_root: Path, bugs: list[tuple[str, str]]) -> tuple[int, Counter[str]]:
    exact_method = 0
    distances: Counter[str] = Counter()
    for system, bug_id in bugs:
        bug_root = benchmark_root / "bugs" / system / bug_id
        report = load_json(bug_root / "report.json")
        blocks = load_json(bug_root / "blocks.json")
        if has_exact_method_anchor(report, blocks):
            exact_method += 1
        distances[classify_crash_distance(report, blocks)] += 1
    return exact_method, distances


def summarize_signals(classifications: dict, names: list[str]) -> list[tuple[str, int]]:
    def entry(name: str) -> dict:
        return classifications.get(name, {})

    return [
        ("Code IDs", sum((entry(name).get("n_code_identifiers") or 0) > 0 for name in names)),
        ("Prior analysis", sum(bool(entry(name).get("has_analysis")) for name in names)),
        ("Stack traces", sum(bool(entry(name).get("has_stack_traces")) for name in names)),
        ("Causal cues", sum((entry(name).get("n_causal_refs") or 0) > 0 for name in names)),
        (
            "Issue refs.",
            sum(bool(entry(name).get("jira_refs") or entry(name).get("gh_issue_refs")) for name in names),
        ),
        ("Logs", sum(bool(entry(name).get("has_logs")) for name in names)),
        ("Artifacts", sum((entry(name).get("n_artifacts") or 0) > 0 for name in names)),
    ]


def draw_summary(ax, rows: list[tuple[str, str]]) -> None:
    ax.axis("off")
    ax.set_title("A. Release / labels", loc="left", fontsize=8.0, weight="bold", pad=4)

    y = 0.90
    ax.text(0.00, y, "Statistic", transform=ax.transAxes, fontsize=6.7, weight="bold")
    ax.text(0.98, y, "Value", transform=ax.transAxes, fontsize=6.7, weight="bold", ha="right")
    ax.plot([0, 1], [y - 0.035, y - 0.035], transform=ax.transAxes, color="black", lw=0.8)

    y -= SUMMARY_HEADER_GAP
    for key, value in rows:
        ax.text(0.00, y, key, transform=ax.transAxes, fontsize=6.45)
        ax.text(0.98, y, value, transform=ax.transAxes, fontsize=6.45, ha="right")
        y -= SUMMARY_ROW_STEP


def draw_signal_bars(ax, signals: list[tuple[str, int]], total: int) -> None:
    labels = [label for label, _ in signals]
    counts = np.array([count for _, count in signals])
    values = np.array([pct(int(count), total) for count in counts])
    positions = np.arange(len(labels))[::-1] * ROW_STEP

    ax.barh(
        positions,
        [100] * len(labels),
        color=BAR_BG,
        height=BAR_HEIGHT,
        edgecolor="white",
        linewidth=0.35,
    )
    ax.barh(
        positions,
        values,
        color="#4f8b75",
        height=BAR_HEIGHT,
        edgecolor="white",
        linewidth=0.35,
    )
    for y, value, count in zip(positions, values, counts):
        ax.text(min(value + 2.0, 96), y, f"{count}/{total}", va="center", fontsize=6.25)

    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=6.4)
    ax.set_xlim(0, 105)
    ax.set_ylim(
        positions.min() - BAR_HEIGHT / 2 - BAR_Y_BOTTOM_PAD,
        positions.max() + BAR_HEIGHT / 2 + BAR_Y_TOP_PAD,
    )
    ax.set_xticks([0, 50, 100])
    ax.set_xticklabels(["0%", "50%", "100%"], fontsize=6.1)
    ax.set_title("B. Issue-side evidence", loc="left", fontsize=8.0, weight="bold", pad=4)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", length=2, color="#9aa1aa")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#c0c4c8")
    ax.spines["bottom"].set_linewidth(0.6)


def draw_localization_bars(ax, exact_method: int, distances: Counter[str], total: int) -> None:
    rows = [
        ("No exact method", total - exact_method, "#b7684c"),
        ("Exact method", exact_method, "#d69a77"),
        ("No stack trace", distances["no_stack_trace"], "#3e71a7"),
        ("Cross-component", distances["cross_component"], "#3e71a7"),
        ("Co-located", distances["co_located"], "#3e71a7"),
        ("Adjacent", distances["adjacent"], "#3e71a7"),
    ]
    labels = [row[0] for row in rows]
    counts = np.array([row[1] for row in rows])
    values = np.array([pct(int(count), total) for count in counts])
    colors = [row[2] for row in rows]
    positions = np.arange(len(labels))[::-1] * ROW_STEP

    ax.barh(
        positions,
        [100] * len(labels),
        color=BAR_BG,
        height=BAR_HEIGHT,
        edgecolor="white",
        linewidth=0.35,
    )
    ax.barh(
        positions,
        values,
        color=colors,
        height=BAR_HEIGHT,
        edgecolor="white",
        linewidth=0.35,
    )
    for y, value, count in zip(positions, values, counts):
        ax.text(min(value + 2.0, 96), y, f"{count}/{total}", va="center", fontsize=6.25)

    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=6.25)
    ax.set_xlim(0, 105)
    ax.set_ylim(
        positions.min() - BAR_HEIGHT / 2 - BAR_Y_BOTTOM_PAD,
        positions.max() + BAR_HEIGHT / 2 + BAR_Y_TOP_PAD,
    )
    ax.set_xticks([0, 50, 100])
    ax.set_xticklabels(["0%", "50%", "100%"], fontsize=6.1)
    ax.set_title("C. Localization difficulty", loc="left", fontsize=8.0, weight="bold", pad=4)
    ax.tick_params(axis="y", length=0)
    ax.tick_params(axis="x", length=2, color="#9aa1aa")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.spines["bottom"].set_visible(True)
    ax.spines["bottom"].set_color("#c0c4c8")
    ax.spines["bottom"].set_linewidth(0.6)


def shrink_bar_axes_to_table_height(axes) -> None:
    for ax in axes:
        pos = ax.get_position()
        top = pos.y1
        height = pos.height * BAR_AXES_HEIGHT_SCALE
        ax.set_position([pos.x0, top - height, pos.width, height])


def build_figure(benchmark_root: Path, classifications_path: Path, output: Path) -> None:
    bugs = bug_dirs(benchmark_root)
    total = len(bugs)
    systems = Counter(system for system, _ in bugs)
    gt = summarize_ground_truth(benchmark_root, bugs)
    exact_method, distances = summarize_difficulty(benchmark_root, bugs)
    classifications = load_json(classifications_path)
    signals = summarize_signals(classifications, [f"{system}-{bug_id}" for system, bug_id in bugs])

    summary_rows = [
        ("Incidents", f"{total}"),
        ("Systems", f"{len(systems)}"),
        ("Gold blocks", f"{gt['total_blocks']}"),
        ("Avg. blocks", f"{gt['avg_blocks']:.1f}"),
        ("Median blocks", f"{gt['median_blocks']:.0f}"),
        ("Multi-block", count_pct_short(gt["multi_block"], total)),
        ("Cross-file", count_pct_short(gt["cross_file"], total)),
        ("Med. lines", f"{gt['median_span']:.0f}"),
    ]

    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 7,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(7.8, 1.9),
        gridspec_kw={"width_ratios": [0.9, 1.25, 1.25], "wspace": 0.9},
    )
    draw_summary(axes[0], summary_rows)
    draw_signal_bars(axes[1], signals, total)
    draw_localization_bars(axes[2], exact_method, distances, total)
    shrink_bar_axes_to_table_height(axes[1:])

    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    default_repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Plot DistDebug-Bench workload characterization.")
    parser.add_argument(
        "--benchmark-root",
        type=Path,
        default=default_repo_root / "benchmark",
        help="Path to the benchmark directory.",
    )
    parser.add_argument(
        "--classifications",
        type=Path,
        default=default_repo_root / "benchmark" / "bug_classifications.json",
        help="Path to the checked-in bug classification JSON.",
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output figure path.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    build_figure(args.benchmark_root, args.classifications, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

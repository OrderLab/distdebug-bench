#!/usr/bin/env python3
"""
Visualize block comparison stats from collection-pipeline/stats.

Parses the stats file and generates:
  - Precision by issue (bar chart per project)
  - Line precision by issue (bar chart per project)
  - Summary metrics: containment, function coverage, overall precision (across projects)
  - Heatmap or grouped bar of macro metrics by project

Requires: matplotlib (install with: pip install matplotlib)
"""

import re
from pathlib import Path
from typing import Any

# Try to import matplotlib
try:
    import matplotlib.pyplot as plt
    import matplotlib

    matplotlib.use("Agg")
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def parse_stats(stats_path: str | Path) -> dict[str, Any]:
    """Parse the stats file and return structured data."""
    path = Path(stats_path)
    text = path.read_text()
    data: dict[str, Any] = {}

    # Split by project sections (=== Project: X vs Y ===)
    project_pattern = re.compile(
        r"={60}\s*\n\s*(ZooKeeper|etcd):\s*([^\n]+)\n={60}",
        re.MULTILINE,
    )
    sections = list(project_pattern.finditer(text))

    for i, match in enumerate(sections):
        project_name = match.group(1).lower()
        comparison = match.group(2).strip()
        start = match.end()
        end = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        section_text = text[start:end]

        project_data: dict[str, Any] = {
            "comparison": comparison,
            "precision_by_issue": {},
            "line_precision_by_issue": {},
            "containment": {},
            "function_coverage": {},
            "overall_precision": None,
            "macro_precision": None,
            "overall_line_precision": None,
            "macro_line_precision": None,
        }

        # Precision per issue: "  ZOOKEEPER-2581: precision = 2/3 = 66.67%"
        for m in re.finditer(
            r"\s+([A-Z]+-\d+):\s+precision\s*=\s*\d+/\d+\s*=\s*([\d.]+)%",
            section_text,
        ):
            issue_id, pct = m.group(1), float(m.group(2))
            project_data["precision_by_issue"][issue_id] = pct

        # Overall precision: "  Overall precision (micro): 17/71 = 23.94%"
        m = re.search(
            r"Overall precision \(micro\):\s*\d+/\d+\s*=\s*([\d.]+)%",
            section_text,
        )
        if m:
            project_data["overall_precision"] = float(m.group(1))

        # Macro precision: "  Macro precision (avg over issues): 43.39%"
        m = re.search(
            r"Macro precision \(avg over issues\):\s*([\d.]+)%",
            section_text,
        )
        if m:
            project_data["macro_precision"] = float(m.group(1))

        # Line precision per issue: "  ZOOKEEPER-2581: avg line precision = 20.00%"
        for m in re.finditer(
            r"\s+([A-Z]+-\d+):\s+avg line precision\s*=\s*([\d.]+)%",
            section_text,
        ):
            issue_id, pct = m.group(1), float(m.group(2))
            project_data["line_precision_by_issue"][issue_id] = pct

        # Overall line precision
        m = re.search(
            r"Overall line precision \(micro[^)]*\):\s*([\d.]+)%",
            section_text,
        )
        if m:
            project_data["overall_line_precision"] = float(m.group(1))

        m = re.search(
            r"Macro line precision \(avg of per-issue avg\):\s*([\d.]+)%",
            section_text,
        )
        if m:
            project_data["macro_line_precision"] = float(m.group(1))

        # Containment: "  Benchmark blocks total: 22" etc.
        m = re.search(r"Benchmark blocks total:\s*(\d+)", section_text)
        if m:
            project_data["containment"]["total"] = int(m.group(1))
        m = re.search(r"Contained in tempt_blocks:\s*(\d+)", section_text)
        if m:
            project_data["containment"]["contained"] = int(m.group(1))
        m = re.search(r"NOT contained \(missing\):\s*(\d+)", section_text)
        if m:
            project_data["containment"]["missing"] = int(m.group(1))

        # Function coverage
        m = re.search(
            r"Function coverage:\s*\d+/\d+\s*=\s*([\d.]+)%",
            section_text,
        )
        if m:
            project_data["function_coverage"]["pct"] = float(m.group(1))
        m = re.search(
            r"Function covered \(correct function given\):\s*(\d+)", section_text
        )
        if m:
            project_data["function_coverage"]["covered"] = int(m.group(1))
        m = re.search(r"Function NOT covered:\s*(\d+)", section_text)
        if m:
            project_data["function_coverage"]["not_covered"] = int(m.group(1))

        data[project_name] = project_data

    return data


def plot_precision_by_issue(data: dict, out_dir: Path) -> None:
    """Bar chart of precision per issue for each project."""
    for project, pd in data.items():
        if not pd.get("precision_by_issue"):
            continue
        issues = list(pd["precision_by_issue"].keys())
        values = list(pd["precision_by_issue"].values())
        fig, ax = plt.subplots(figsize=(max(8, len(issues) * 0.4), 5))
        colors = [
            "#2ecc71" if v >= 50 else "#f39c12" if v > 0 else "#e74c3c" for v in values
        ]
        ax.bar(issues, values, color=colors, edgecolor="gray", linewidth=0.5)
        ax.set_ylabel("Precision (%)")
        ax.set_xlabel("Issue ID")
        ax.set_title(f"{project.title()} — Precision by Issue")
        ax.set_ylim(0, 105)
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        fig.savefig(out_dir / f"precision_by_issue_{project}.png", dpi=150)
        plt.close()


def plot_line_precision_by_issue(data: dict, out_dir: Path) -> None:
    """Bar chart of line precision per issue for each project."""
    for project, pd in data.items():
        if not pd.get("line_precision_by_issue"):
            continue
        issues = list(pd["line_precision_by_issue"].keys())
        values = list(pd["line_precision_by_issue"].values())
        fig, ax = plt.subplots(figsize=(max(8, len(issues) * 0.4), 5))
        colors = [
            "#3498db" if v >= 20 else "#9b59b6" if v > 0 else "#95a5a6" for v in values
        ]
        ax.bar(issues, values, color=colors, edgecolor="gray", linewidth=0.5)
        ax.set_ylabel("Line precision (%)")
        ax.set_xlabel("Issue ID")
        ax.set_title(f"{project.title()} — Line Precision by Issue")
        ax.set_ylim(0, 105)
        plt.xticks(rotation=45, ha="right")
        plt.tight_layout()
        fig.savefig(out_dir / f"line_precision_by_issue_{project}.png", dpi=150)
        plt.close()


def plot_summary_metrics(data: dict, out_dir: Path) -> None:
    """Grouped bar chart: containment, function coverage, precision by project."""
    projects = list(data.keys())
    metrics = [
        "Containment (%)",
        "Function coverage (%)",
        "Overall precision (%)",
        "Line precision (%)",
    ]

    containment_pct = []
    func_coverage_pct = []
    overall_prec = []
    line_prec = []

    for p in projects:
        pd = data[p]
        c = pd.get("containment", {})
        if c.get("total", 0) > 0:
            containment_pct.append(100.0 * c.get("contained", 0) / c["total"])
        else:
            containment_pct.append(0)
        func_coverage_pct.append(pd.get("function_coverage", {}).get("pct", 0))
        overall_prec.append(pd.get("overall_precision") or 0)
        line_prec.append(pd.get("overall_line_precision") or 0)

    x = range(len(projects))
    width = 0.2

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.bar([i - 1.5 * width for i in x], containment_pct, width, label="Containment")
    ax.bar(
        [i - 0.5 * width for i in x],
        func_coverage_pct,
        width,
        label="Function coverage",
    )
    ax.bar([i + 0.5 * width for i in x], overall_prec, width, label="Overall precision")
    ax.bar([i + 1.5 * width for i in x], line_prec, width, label="Line precision")

    ax.set_ylabel("Percent (%)")
    ax.set_title("Summary Metrics by Project")
    ax.set_xticks(x)
    ax.set_xticklabels([p.title() for p in projects])
    ax.legend(loc="upper right")
    ax.set_ylim(0, 105)
    plt.tight_layout()
    fig.savefig(out_dir / "summary_metrics_by_project.png", dpi=150)
    plt.close()


def plot_containment_pie(data: dict, out_dir: Path) -> None:
    """Pie charts for containment (contained vs missing) per project."""
    for project, pd in data.items():
        c = pd.get("containment", {})
        contained = c.get("contained", 0)
        missing = c.get("missing", 0)
        if contained + missing == 0:
            continue
        fig, ax = plt.subplots(figsize=(5, 5))
        ax.pie(
            [contained, missing],
            labels=["Contained", "Missing"],
            autopct="%1.1f%%",
            colors=["#2ecc71", "#e74c3c"],
            explode=(0.02, 0),
        )
        ax.set_title(f"{project.title()} — Benchmark Block Containment")
        fig.savefig(out_dir / f"containment_{project}.png", dpi=150)
        plt.close()


def main() -> None:
    if not HAS_MATPLOTLIB:
        print("Error: matplotlib is required. Install with: pip install matplotlib")
        raise SystemExit(1)

    script_dir = Path(__file__).resolve().parent.parent
    stats_path = script_dir / "stats"
    out_dir = script_dir / "output" / "stats_viz"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not stats_path.exists():
        print(f"Error: stats file not found at {stats_path}")
        print("Run: utils/compare_blocks.py and redirect output to stats")
        raise SystemExit(1)

    data = parse_stats(stats_path)
    if not data:
        print("Error: no project data parsed from stats file")
        raise SystemExit(1)

    print(f"Parsed {len(data)} project(s): {list(data.keys())}")
    plot_precision_by_issue(data, out_dir)
    plot_line_precision_by_issue(data, out_dir)
    plot_summary_metrics(data, out_dir)
    plot_containment_pie(data, out_dir)

    print(f"Saved visualizations to {out_dir}")
    for f in sorted(out_dir.iterdir()):
        print(f"  - {f.name}")


if __name__ == "__main__":
    main()

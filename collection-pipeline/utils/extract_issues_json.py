#!/usr/bin/env python3
"""
Extract issues from issues.csv into per-project JSON files in the same format as
zookeeper_jira_issues.json / kubernetes_github_issues.json.

Output: one JSON file per project (e.g. zookeeper_jira_issues.json, kafka_jira_issues.json,
hdfs_jira_issues.json, etc.) in the directory containing issues.csv (or --output-dir).
"""

import argparse
import csv
import json
import re
import os
from collections import defaultdict


# Jira: PROJECT-NUM (e.g. KAFKA-17062, HDFS-13101, ZOOKEEPER-2775)
# GitHub: numeric ID (e.g. 135981 for kubernetes)
JIRA_KEY_PATTERN = re.compile(r"([A-Z][A-Z0-9]+-\d+)")
GITHUB_PR_PATTERN = re.compile(r"github\.com/[^/]+/[^/]+/pull/(\d+)")
GITHUB_ISSUE_PATTERN = re.compile(r"github\.com/[^/]+/[^/]+/issues/(\d+)")


def extract_issue_key(issue_link: str, system: str, issue_id: str) -> str:
    """Derive the issue key for the JSON output (e.g. KAFKA-17062 or 135981)."""
    if not issue_link or not str(issue_link).strip():
        # Fallback: Jira-style SYSTEM-NUM (uppercase system)
        norm = (system or "").strip().upper()
        if norm and str(issue_id).strip().isdigit():
            return f"{norm}-{issue_id.strip()}"
        return str(issue_id).strip() or "unknown"

    link = issue_link.strip()

    # Jira: full URL or bare "PROJECT-NUM"
    m = JIRA_KEY_PATTERN.search(link)
    if m:
        return m.group(1)

    # GitHub PR
    m = GITHUB_PR_PATTERN.search(link)
    if m:
        return m.group(1)
    m = GITHUB_ISSUE_PATTERN.search(link)
    if m:
        return m.group(1)

    # Fallback
    norm = (system or "").strip().upper()
    if norm and str(issue_id).strip().isdigit():
        return f"{norm}-{issue_id.strip()}"
    return str(issue_id).strip() or "unknown"


def is_github_project(issue_links: list) -> bool:
    """Heuristic: if any link is a GitHub URL, treat project as GitHub."""
    for link in issue_links:
        if link and "github" in link:
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="Extract issues.csv into per-project JSON files")
    ap.add_argument(
        "csv_path",
        nargs="?",
        default=os.path.join(os.path.dirname(__file__), "..", "issues.csv"),
        help="Path to issues.csv",
    )
    ap.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="Directory for output JSON files (default: same as csv_path)",
    )
    args = ap.parse_args()

    csv_path = os.path.abspath(args.csv_path)
    output_dir = os.path.abspath(args.output_dir) if args.output_dir else os.path.dirname(csv_path)

    if not os.path.isfile(csv_path):
        raise SystemExit(f"CSV not found: {csv_path}")

    # CSV columns (from header)
    # System, ID, Issue Creation Date, Issue Link, Fix Link, Branch, Title, Description, ...
    # Comment, Column 1
    # Group by normalized system name; value = list of (issue_key, issue_dict, issue_link)
    by_system = defaultdict(list)

    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SystemExit("CSV has no header")
        for row in reader:
            system = (row.get("System") or "").strip()
            issue_id = (row.get("ID") or "").strip()
            issue_link = (row.get("Issue Link") or "").strip()
            title = (row.get("Title") or "").strip()
            description = (row.get("Description") or "").strip()
            comment = (row.get("Comment") or "").strip()

            if not system and not issue_id and not issue_link:
                continue

            key = extract_issue_key(issue_link, system, issue_id)
            system_norm = (system or "unknown").strip().lower()
            issue_dict = {
                "url": issue_link,
                "title": title,
                "description": description,
                "comments": [comment] if comment else [],
            }
            by_system[system_norm].append((key, issue_dict, issue_link))

    os.makedirs(output_dir, exist_ok=True)
    for system_key, entries in sorted(by_system.items()):
        issue_links = [e[2] for e in entries]
        suffix = "github_issues" if is_github_project(issue_links) else "jira_issues"
        basename = f"{system_key}_{suffix}"
        issues = {key: d for key, d, _ in entries}
        out_path = os.path.join(output_dir, f"{basename}.json")
        with open(out_path, "w", encoding="utf-8") as out:
            json.dump(issues, out, indent=2, ensure_ascii=False)
        print(f"Wrote {len(issues)} issues -> {out_path}")


if __name__ == "__main__":
    main()

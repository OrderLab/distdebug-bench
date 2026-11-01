from .mydiff import main as mydiff_main
from dotenv import load_dotenv
import json
import sys
from pathlib import Path
from .fetch_github_issue_patches import (
    update_json_with_pr,
    parse_issue_url,
    project_slug_from_issues_path,
    fetch_issue_patch,
)
import requests
import os

ENV = load_dotenv()


def generate_diff_files(
    project: str,
    git_repo_path: str,
    output_dir: str,
    token: str = None,
    json_path: str = None,
    project_name: str = None,
):
    if project_name == "etcd":
        token = (token or "").strip() or None

        issues_path = Path(json_path)
        if not issues_path.is_file():
            print(f"Error: Issues file not found: {issues_path}", file=sys.stderr)
            sys.exit(1)

        with open(issues_path, encoding="utf-8") as f:
            issues_data = json.load(f)

        if not issues_data:
            print("No issues in file.")
            return

        first = next(iter(issues_data.values()))
        url = first.get("url") if isinstance(first, dict) else None
        if not url:
            print(
                "Error: Issue entries must have a 'url' field.",
                file=sys.stderr,
            )
            sys.exit(1)
        parsed = parse_issue_url(url)
        if not parsed:
            print("Error: Could not parse owner/repo from issue URL.", file=sys.stderr)
            sys.exit(1)
        owner, repo = parsed

        project = project_name or project_slug_from_issues_path(str(issues_path))
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        issue_ids = list(issues_data.keys())
        print(f"Project: {owner}/{repo} (slug: {project})")
        print(f"Issues: {len(issue_ids)} from {issues_path}")
        print(f"Output: {out_dir}\n")

        saved = 0
        issue_to_pr: list[tuple[str, int, str | None]] = []

        for i, issue_id in enumerate(issue_ids):
            if (out_dir / f"{issue_id}.diff").exists():
                continue
            try:
                print(f"[{i+1}/{len(issue_ids)}] ", end="", flush=True)
                result = fetch_issue_patch(
                    owner,
                    repo,
                    issue_id,
                    token,
                    out_dir,
                    project_name=project,
                    verbose=True,
                    json_path=issues_path,
                    git_repo_path=git_repo_path,
                )
                if result:
                    _issue_id, merged_pr, _out_path, _commit, _parent_commit = result

                    update_json_with_pr(
                        issues_path, _issue_id, merged_pr, _commit, _parent_commit
                    )
                    issue_to_pr.append((_issue_id, merged_pr, _commit, _parent_commit))
                    saved += 1
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 403:
                    print("rate limit exceeded (use GITHUB_TOKEN for higher limits)")
                    break
                raise

        if issue_to_pr:
            print("\nMerging PR per issue:")
            for issue_id, pr_num, commit, parent_commit in issue_to_pr:
                pr_url = f"https://github.com/{owner}/{repo}/pull/{pr_num}"
                print(
                    f"  Issue #{issue_id}  ->  PR #{pr_num}  {pr_url}  {commit}  {parent_commit}"
                )

        print(f"\nSaved {saved} diffs")

    mydiff_main(json_path, output_dir, git_repo_path, project_name)

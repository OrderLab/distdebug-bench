#!/usr/bin/env python3
"""
Fetch patches (diffs) for GitHub issues by finding the pull requests that close them.

For each issue in a project's GitHub issues JSON (e.g. output/issues/etcd_github_issues.json),
uses the GitHub API to:
  1. Get the issue timeline and find PRs that reference/close the issue (cross-referenced)
  2. If none, fallback: search for merged PRs that mention the issue number
  3. For each merged PR, download the PR diff (patch)
  4. Save diffs under diffs/<project>/ (e.g. diffs/etcd/13192.diff)

Uses GITHUB_TOKEN env var for authentication (optional but recommended for higher rate limits).

Example (etcd):
  python utils/fetch_github_issue_patches.py \\
    --issues output/issues/etcd_github_issues.json \\
    --diffs-dir diffs
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.mydiff import find_commit_for_issue, get_diff

from utils.github_search import get_pull_requests_linked_to_issue

import requests

DEFAULT_DIFFS_DIR = PROJECT_ROOT / "diffs"

GITHUB_API = "https://api.github.com"
TIMELINE_ACCEPT = "application/vnd.github+json"
DIFF_ACCEPT = "application/vnd.github.v3.diff"


def parse_issue_url(url: str) -> tuple[str, str] | None:
    """Extract owner and repo from a GitHub issue URL. Returns (owner, repo) or None."""
    m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/", url)
    if m:
        return (m.group(1), m.group(2))
    return None


def get_headers(token: str | None) -> dict:
    h = {"Accept": TIMELINE_ACCEPT, "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_issue_timeline(
    owner: str, repo: str, issue_number: str, token: str | None
) -> list:
    """Fetch timeline events for an issue. Returns list of events."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{issue_number}/timeline"
    events = []
    page = 1
    while True:
        r = requests.get(
            url,
            headers=get_headers(token),
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        if r.status_code == 404:
            return []
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            break
        if not data:
            break
        events.extend(data)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)
    return events


def search_merged_prs_referencing_issue(
    owner: str, repo: str, issue_number: str, token: str | None
) -> list[int]:
    """
    Fallback: use Search API to find merged PRs that reference this issue.
    Use when timeline returns no cross-referenced events (e.g. without auth).
    """
    url = f"{GITHUB_API}/search/issues"
    q = f"repo:{owner}/{repo} type:pr is:merged {issue_number}"
    numbers = []
    page = 1
    while True:
        r = requests.get(
            url,
            headers=get_headers(token),
            params={"q": q, "per_page": 100, "page": page},
            timeout=30,
        )
        if r.status_code != 200:
            return numbers
        data = r.json()
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            break
        for item in items:
            num = item.get("number")
            if num is not None:
                numbers.append(num)
        if len(items) < 100:
            break
        page += 1
        time.sleep(0.4)
    return numbers


def pr_numbers_from_timeline(events: list) -> list[int]:
    """
    From timeline events, collect issue/PR numbers that reference this issue.
    (Cross-referenced events include PRs; we verify via the pulls API.)
    """
    numbers = []
    seen = set()
    for ev in events:
        if ev.get("event") != "cross-referenced":
            continue
        source = ev.get("source") or {}
        if source.get("type") != "issue":
            continue
        issue = source.get("issue") or {}
        num = issue.get("number")
        if num is not None and num not in seen:
            seen.add(num)
            numbers.append(num)
    return numbers


def get_pr_details(
    owner: str, repo: str, pr_number: int, token: str | None
) -> dict | None:
    """Get PR details. Returns None if not found or not merged."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"
    r = requests.get(url, headers=get_headers(token), timeout=30)
    if r.status_code != 200:
        return None
    pr = r.json()
    if not pr.get("merged_at"):
        return None
    return pr


def get_pr_diff(owner: str, repo: str, pr_number: int, token: str | None) -> str | None:
    """Fetch the diff (patch) for a pull request. Returns raw diff text or None."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"
    r = requests.get(
        url,
        headers={**get_headers(token), "Accept": DIFF_ACCEPT},
        timeout=60,
    )
    if r.status_code != 200:
        return None
    return r.text


def get_commit_sha_before_pr_via_git_log(
    git_repo_path: str | Path, pr_number: int
) -> str | None:
    """
    Using local git, search for the merge commit with message
    "Merge pull request #<pr_number>" and return the commit SHA of its first
    parent (the commit that was at the base before that PR was merged).

    Runs:
      git log --all --grep="Merge pull request #<pr_number>" --format=%H -1
      git rev-parse <merge_sha>^

    Returns:
      40-char SHA of the commit before the merge, or None if not found.
    """
    git_repo_path = Path(git_repo_path)
    if not (git_repo_path / ".git").exists():
        return None
    grep = f"Merge pull request #{pr_number}"
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(git_repo_path),
                "log",
                "--all",
                f"--grep={grep}",
                "--format=%H%S%H",
                "-1",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return None
        merge_sha = out.stdout.strip().splitlines()[0].strip()
        if not merge_sha or len(merge_sha) != 40:
            return None
        parent_out = subprocess.run(
            ["git", "-C", str(git_repo_path), "rev-parse", f"{merge_sha}^"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        print(merge_sha, parent_out.stdout.strip())
        if parent_out.returncode != 0 or not parent_out.stdout.strip():
            return None
        return parent_out.stdout.strip().splitlines()[0].strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


# ---------------------------------------------------------------------------
# Stable version before PR merge (last release that does not contain the fix)
# ---------------------------------------------------------------------------


def list_releases(
    owner: str, repo: str, token: str | None, include_prerelease: bool = False
) -> list[dict]:
    """List repository releases, newest first by published_at. Excludes prereleases by default."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/releases"
    releases = []
    page = 1
    while True:
        r = requests.get(
            url,
            headers=get_headers(token),
            params={"per_page": 100, "page": page},
            timeout=30,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            break
        for rel in data:
            if not include_prerelease and rel.get("prerelease"):
                continue
            if rel.get("draft"):
                continue
            releases.append(rel)
        if len(data) < 100:
            break
        page += 1
        time.sleep(0.2)
    releases.sort(
        key=lambda x: (x.get("published_at") or x.get("created_at") or ""), reverse=True
    )
    return releases


def get_commit_sha_for_ref(
    owner: str, repo: str, ref: str, token: str | None
) -> str | None:
    """Resolve a ref (tag name or branch) to commit SHA."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits/{ref}"
    r = requests.get(url, headers=get_headers(token), timeout=30)
    if r.status_code != 200:
        return None
    body = r.json()
    return body.get("sha") if isinstance(body, dict) else None


def compare_commits(
    owner: str, repo: str, base_sha: str, head_sha: str, token: str | None
) -> str | None:
    """Compare base...head. Returns status: 'ahead' | 'behind' | 'identical' | 'diverged'."""
    url = f"{GITHUB_API}/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
    r = requests.get(url, headers=get_headers(token), timeout=30)
    if r.status_code != 200:
        return None
    body = r.json()
    return body.get("status") if isinstance(body, dict) else None


def get_stable_version_before_commit(
    owner: str,
    repo: str,
    merge_commit_sha: str,
    token: str | None,
    *,
    releases_cache: list[dict] | None = None,
    tag_sha_cache: dict[str, str] | None = None,
) -> str | None:
    """
    Return the tag_name of the most recent (by published_at) release that does NOT
    contain the given commit (i.e. the bug is still present in that version).

    Uses GitHub Compare API: for each release tag we check if merge is "ahead" of
    the tag; if so, the tag does not contain the fix.
    """
    if not merge_commit_sha:
        return None
    releases = (
        releases_cache
        if releases_cache is not None
        else list_releases(owner, repo, token)
    )
    tag_cache: dict[str, str] = {} if tag_sha_cache is None else dict(tag_sha_cache)
    for rel in releases:
        tag_name = rel.get("tag_name")
        if not tag_name:
            continue
        if tag_name not in tag_cache:
            tag_sha = get_commit_sha_for_ref(owner, repo, tag_name, token)
            time.sleep(0.12)
            if not tag_sha:
                continue
            tag_cache[tag_name] = tag_sha
        tag_sha = tag_cache[tag_name]
        status = compare_commits(owner, repo, tag_sha, merge_commit_sha, token)
        time.sleep(0.12)
        if status == "ahead":
            return tag_name
    return None


def get_stable_version_before_pr(
    owner: str,
    repo: str,
    pr_number: int,
    token: str | None,
    *,
    releases_cache: list[dict] | None = None,
    tag_sha_cache: dict[str, str] | None = None,
) -> str | None:
    """
    Get the stable version (release tag) that does not yet contain the merge
    from the given pull request. Uses the List pull requests / Get a pull request
    API to obtain merge_commit_sha, then finds the latest release that doesn't
    include that commit.

    Returns:
        Tag name (e.g. "v3.5.0") of the last release without the fix, or None.
    """
    pr = get_pr_details(owner, repo, pr_number, token)
    if not pr:
        return None
    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        return None
    return get_stable_version_before_commit(
        owner,
        repo,
        merge_sha,
        token,
        releases_cache=releases_cache,
        tag_sha_cache=tag_sha_cache,
    )


def parse_gitlog_commit_date(
    gitlog_path: str | Path, commit_sha: str
) -> datetime | None:
    """
    Parse a gitlog file (output of `git log` or similar) and return the commit
    date for the given SHA if present. The file format is expected to have
    blocks like:

        commit <sha>
        Author: ...
        Date:   <date>
        ...
    """
    gitlog_path = Path(gitlog_path)
    if not gitlog_path.is_file():
        return None
    commit_sha = commit_sha.strip().lower()
    current_sha = None
    date_re = re.compile(r"^Date:\s*(.+)$")
    # Git log format: "Date:   Wed Jun 11 01:36:56 2025 +0800"
    for line in gitlog_path.open(encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if line.startswith("commit "):
            current_sha = line[7:].strip().lower()
            continue
        if current_sha and current_sha == commit_sha:
            m = date_re.match(line.strip())
            if m:
                date_str = m.group(1).strip()
                try:
                    # Parse "Wed Jun 11 01:36:56 2025 +0800"
                    return datetime.strptime(date_str[:24], "%a %b %d %H:%M:%S %Y")
                except ValueError:
                    pass
                current_sha = None  # one Date line per block; skip rest of block
    return None


def get_stable_version_before_commit_using_gitlog(
    owner: str,
    repo: str,
    merge_commit_sha: str,
    token: str | None,
    gitlog_path: str | Path,
    *,
    releases_cache: list[dict] | None = None,
) -> str | None:
    """
    Same as get_stable_version_before_commit, but use the commit date from a
    local gitlog file to pick the latest release with published_at before that
    date. Avoids one API call per release (compare); needs only list_releases.
    Falls back to get_stable_version_before_commit if the commit is not in the
    gitlog or date parsing fails.
    """
    commit_date = parse_gitlog_commit_date(gitlog_path, merge_commit_sha)
    releases = (
        releases_cache
        if releases_cache is not None
        else list_releases(owner, repo, token)
    )
    if commit_date and releases:
        # Find latest release published before this commit (releases are newest first)
        commit_naive = (
            commit_date.replace(tzinfo=None) if commit_date.tzinfo else commit_date
        )
        for rel in releases:
            pub = rel.get("published_at") or rel.get("created_at")
            if not pub:
                continue
            try:
                rel_time = datetime.fromisoformat(pub.replace("Z", "+00:00"))
            except ValueError:
                continue
            rel_naive = rel_time.replace(tzinfo=None) if rel_time.tzinfo else rel_time
            if rel_naive < commit_naive:
                tag = rel.get("tag_name")
                if tag:
                    return tag
    return get_stable_version_before_commit(
        owner, repo, merge_commit_sha, token, releases_cache=releases
    )


def get_stable_version_before_commit_via_git(
    git_repo_path: str | Path, merge_commit_sha: str
) -> str | None:
    """
    Using a local git repository, find the latest tag that does NOT contain the
    given commit. Runs `git tag -l` and for each tag checks whether the commit
    is an ancestor of the tag; returns the latest such tag (by version sort).

    Returns tag name (e.g. "release-3.9.2") or None.
    """
    git_repo_path = Path(git_repo_path)
    if not (git_repo_path / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(git_repo_path), "tag", "-l"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if out.returncode != 0:
            return None
        tags = [t.strip() for t in out.stdout.splitlines() if t.strip()]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    # Prefer version-like tags (v* or release-*), sort by version if possible
    def version_sort_key(t: str) -> tuple:
        # Heuristic: (is_release, negated_version_components, tag_string)
        t_lower = t.lower()
        if t_lower.startswith("v") and t[1:2].isdigit():
            rest = t[1:]
        elif "release-" in t_lower or t_lower.startswith("rel-"):
            rest = t.split("-")[-1] if "-" in t else t
        else:
            rest = t
        parts = re.findall(r"\d+", rest)
        nums = tuple(int(x) for x in parts) if parts else (0,)
        return (0, tuple(-n for n in nums), t)

    tags_sorted = sorted(tags, key=version_sort_key, reverse=True)
    for tag in tags_sorted:
        try:
            r = subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_repo_path),
                    "merge-base",
                    "--is-ancestor",
                    merge_commit_sha,
                    tag,
                ],
                capture_output=True,
                timeout=10,
            )
            # merge-base --is-ancestor A B => 0 if A is ancestor of B (tag contains commit)
            if r.returncode != 0:
                return tag
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def project_slug_from_issues_path(issues_path: str) -> str:
    """Derive project name from filename, e.g. etcd_github_issues.json -> etcd."""
    name = Path(issues_path).stem
    if name.endswith("_github_issues"):
        return name.replace("_github_issues", "")
    return name


def update_json_with_pr(
    json_path: Path,
    issue_id: str,
    pr_number: int = None,
    commit: str | None = None,
    parent_commit: str | None = None,
):
    with open(json_path, "r") as f:
        data = json.load(f)
    if pr_number:
        data[issue_id]["pr_number"] = pr_number
    if commit:
        data[issue_id]["commit"] = commit
    if parent_commit:
        data[issue_id]["parent_commit"] = parent_commit
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)


def fetch_issue_patch(
    owner: str,
    repo: str,
    issue_id: str,
    token: str | None,
    out_dir: Path,
    project_name: str,
    *,
    verbose: bool = True,
    json_path: Path | None = None,
    git_repo_path: str | Path | None = None,
) -> tuple[str, int, Path, str | None] | None:
    """
    Find the merged PR that fixes the given issue, download its diff, and save to out_dir.

    Uses timeline cross-references first, then Search API fallback.

    Args:
        owner: GitHub repo owner (e.g. etcd-io).
        repo: GitHub repo name (e.g. etcd).
        issue_id: Issue number as string (e.g. "13192").
        token: Optional GitHub token for auth.
        out_dir: Directory to write <issue_id>.diff into.
        verbose: If True, print progress to stdout.

    Returns:
        On success: (issue_id, merged_pr_number, path_to_diff_file).
        On failure: None.
    """
    if verbose:
        print(f"Issue #{issue_id}...", end=" ", flush=True)
    time.sleep(0.3)

    dicts = get_pull_requests_linked_to_issue(owner, repo, issue_id, token)
    pr_nums = [d["number"] for d in dicts]
    if not pr_nums:
        print("no GraphQL PRs")
        events = get_issue_timeline(owner, repo, issue_id, token)
        pr_nums = pr_numbers_from_timeline(events)
    if not pr_nums:
        print("no timeline PRs")
        pr_nums = search_merged_prs_referencing_issue(owner, repo, issue_id, token)
    if not pr_nums:
        if verbose:
            print("no referencing PR")
        return None

    merged_pr = None
    for pr_num in pr_nums:
        pr = get_pr_details(owner, repo, pr_num, token)
        if pr:
            merged_pr = pr_num
            break
        time.sleep(0.2)

    if not merged_pr:
        if verbose:
            print("no merged PR")
        return None

    # diff = get_pr_diff(owner, repo, merged_pr, token)
    print("git_repo_path", git_repo_path)
    commit, parent_commit = find_commit_for_issue(
        issue_id,
        git_repo_path=git_repo_path,
        json_path=json_path,
        project_name=project_name,
    )
    print("commit", commit, "parent_commit", parent_commit)
    if commit and parent_commit:
        diff = get_diff(commit, parent_commit, git_repo_path)
    else:
        diff = get_pr_diff(owner, repo, merged_pr, token)

    time.sleep(0.2)
    if not diff:
        if verbose:
            print("failed to fetch diff")
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{issue_id}.diff"
    out_path.write_text(diff, encoding="utf-8")
    if verbose:
        print(f"PR #{merged_pr} -> {out_path}")
    return (issue_id, merged_pr, out_path, commit, parent_commit)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch PR patches for GitHub issues and save to diffs folder.",
    )
    parser.add_argument(
        "--issues",
        "-i",
        default=str(PROJECT_ROOT / "output" / "issues" / "etcd_github_issues.json"),
        help="Path to the project GitHub issues JSON",
    )
    parser.add_argument(
        "--diffs-dir",
        "-o",
        default=str(DEFAULT_DIFFS_DIR),
        help=f"Output directory for .diff files (default: {DEFAULT_DIFFS_DIR})",
    )
    parser.add_argument(
        "--project",
        "-p",
        default=None,
        help="Output subfolder name under --diffs-dir (default: from issues filename)",
    )
    parser.add_argument(
        "--token",
        "-t",
        default=os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub token (default: GITHUB_TOKEN env)",
    )
    parser.add_argument(
        "--git-repo-path",
        "-g",
        default=None,
        help="Path to the git repository",
    )
    args = parser.parse_args()
    token = (args.token or "").strip() or None

    issues_path = Path(args.issues)
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

    project = args.project or project_slug_from_issues_path(str(issues_path))
    out_dir = Path(args.diffs_dir) / project
    out_dir.mkdir(parents=True, exist_ok=True)

    issue_ids = list(issues_data.keys())
    print(f"Project: {owner}/{repo} (slug: {project})")
    print(f"Issues: {len(issue_ids)} from {issues_path}")
    print(f"Output: {out_dir}\n")

    saved = 0
    issue_to_pr: list[tuple[str, int, str | None]] = []

    for i, issue_id in enumerate(issue_ids):
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
                git_repo_path=args.git_repo_path,
            )
            if result:
                _issue_id, merged_pr, _out_path, _commit, _parent_commit = result
                issue_to_pr.append((_issue_id, merged_pr, _commit))
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


if __name__ == "__main__":
    main()

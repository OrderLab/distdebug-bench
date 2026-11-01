import json
import os
import re
import subprocess
import sys

# Git repository path
GIT_REPO_PATH = "/mnt/Ubuntu/zookeeper/"


def run(cmd):
    """Run git command. Use -C GIT_REPO_PATH only when no -C is already in cmd."""
    if cmd[0] == "git" and "-C" not in cmd:
        cmd = ["git", "-C", GIT_REPO_PATH] + cmd[1:]
    return subprocess.check_output(cmd, text=True).strip()


def extract_issue_id(url):
    """Extract issue ID from JIRA URL (e.g., ZOOKEEPER-2405)"""
    match = re.search(r"([A-Z]+-\d+)", url)
    return match.group(1) if match else None


def _is_primary_issue_in_subject(subject, issue_id, project_name):
    """True if the subject line leads with this issue ID (not just mentions it in text)."""
    s = subject.strip()
    if project_name == "zookeeper":
        # Direct prefix: "ZOOKEEPER-3829: fix ..." or "ZOOKEEPER-3829 fix ..."
        if s.startswith(issue_id):
            rest = s[len(issue_id) :]
            return not rest or rest[0] in ": ]-\t"
        # Bracketed: "[ZOOKEEPER-3829] fix ..."
        if s.startswith("[" + issue_id + "]"):
            return True
        return False
    elif project_name == "etcd":
        # todo: add more precise matching logic

        # return s.startswith(f"Merge pull request #{issue_id}")
        return True
    return False


def find_commit_for_issue(
    issue_id,
    max_candidates=50,
    json_path=None,
    git_repo_path=None,
    project_name="zookeeper",
):
    """Find the most recent commit whose subject line leads with the issue ID.
    Skips commits where the ID only appears in the body (e.g. "In the fix of ZOOKEEPER-3829")."""
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
        pr_number = data[issue_id].get("pr_number")
        grep = (
            f"{issue_id}"
            if project_name == "zookeeper"
            else f"Merge pull request #{pr_number}"
        )
        main_branch = "master" if project_name == "zookeeper" else "main"
        out = run(
            [
                "git",
                "-C",
                str(git_repo_path),
                "log",
                main_branch,
                f"--grep={grep}",
                "--format=%H%x00%s",
                "-n",
                str(max_candidates),
            ]
        )
        if not out:
            return None, None
        for line in out.splitlines():
            print("line", line)
            line = line.strip()
            if not line:
                continue
            parts = line.split("\x00", 1)
            commit_hash = parts[0].strip()
            subject = parts[1].strip() if len(parts) > 1 else ""
            if _is_primary_issue_in_subject(subject, issue_id, project_name):
                print("commit_hash", commit_hash, "subject", subject)
                parents = run(
                    [
                        "git",
                        "-C",
                        str(git_repo_path),
                        "log",
                        "--no-walk",
                        "--format=%P",
                        commit_hash,
                    ]
                ).split()
                print("parents", parents)
                if len(parents) > 0:
                    parent_commit_hash = parents[0]
                    print(
                        "merge commit detected, using first parent", parent_commit_hash
                    )
                return commit_hash, parent_commit_hash
        return None, None
    except subprocess.CalledProcessError:
        return None, None


def get_diff(commit, parent_commit, git_repo_path):
    """Get the git diff for a commit"""
    try:
        diff = run(["git", "-C", str(git_repo_path), "diff", parent_commit, commit])
        return diff
    except subprocess.CalledProcessError:
        return None


def main(json_path, output_dir, git_repo_path, project_name):
    # Verify git repository path exists
    if not os.path.exists(git_repo_path):
        print(f"Error: Git repository path not found: {git_repo_path}")
        sys.exit(1)

    # Verify it's a git repository
    if not os.path.exists(os.path.join(git_repo_path, ".git")):
        print(f"Error: {GIT_REPO_PATH} is not a git repository")
        sys.exit(1)

    # Load issues from JSON file
    if not os.path.exists(json_path):
        print(f"Error: {json_path} not found")
        sys.exit(1)

    with open(json_path, "r") as f:
        issues = json.load(f)
        print(issues)  # Create output directory for diffs
    os.makedirs(output_dir, exist_ok=True)

    # Process each issue
    found_count = 0
    not_found_count = 0

    for issue_id, issue in issues.items():
        # for issue_id in issue_ids:

        print(f"Processing {issue_id}...", end=" ")

        # Find commit for this issue
        commit, parent_commit = find_commit_for_issue(
            issue_id,
            git_repo_path=git_repo_path,
            json_path=json_path,
            project_name=project_name,
        )

        if not commit:
            print("Commit not found")
            not_found_count += 1
            continue

        # Get diff
        diff = get_diff(commit, parent_commit, git_repo_path)

        if diff is None:
            print("Could not get diff")
            not_found_count += 1
            continue

        # Save diff to file
        output_file = os.path.join(output_dir, f"{issue_id}.diff")
        with open(output_file, "w") as f:
            f.write(diff)

        print(f"Saved to {output_file}")
        found_count += 1
        with open(json_path, "r") as f:
            data = json.load(f)
        data[issue_id]["commit"] = commit
        data[issue_id]["parent_commit"] = parent_commit
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2)

    print("\nSummary:")
    print(f"  Found commits: {found_count}")
    print(f"  Not found: {not_found_count}")
    print(f"  Total issues: {len(issues)}")


if __name__ == "__main__":
    main("", "output", GIT_REPO_PATH, "zookeeper")
    main("", "output", GIT_REPO_PATH, "etcd")

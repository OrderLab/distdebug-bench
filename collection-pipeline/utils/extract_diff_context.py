#!/usr/bin/env python3
"""
Extract class/function context for each change location in diff files.
Skips test files.
"""

import csv
import json
import os
import re
import sys
import traceback
from ast import parse
from pathlib import Path
from typing import List, Optional, Tuple

import javalang
import tqdm
from .fetch_github_issue_patches import get_commit_sha_before_pr_via_git_log

from dotenv import load_dotenv
from .fqfn_extractor import FQFNExtractor, LangType
from .mydiff import find_commit_for_issue, run
from .language_parser import parse_block_from_dict, ParsedBlock
# Add parent directory to path to import utils (if needed in future)
# sys.path.insert(0, str(Path(__file__).parent.parent))


FQFN_EXTRACTOR = FQFNExtractor(LangType.JAVA)


class Block:
    file_path: str
    start_line: int
    end_line: int
    class_func: str
    docstring: str
    code_block: str

    def __init__(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        class_func: str,
        docstring: str,
        code_block: str,
    ):
        self.file_path = file_path
        self.start_line = start_line
        self.end_line = end_line
        self.class_func = class_func
        self.docstring = docstring
        self.code_block = code_block

    @staticmethod
    def from_parsed_block(parsed_block: ParsedBlock) -> "Block":
        return Block(
            file_path=parsed_block.file_path,
            start_line=parsed_block.start_line,
            end_line=parsed_block.end_line,
            class_func=parsed_block.fqfn,
            docstring=parsed_block.docstring,
            code_block=parsed_block.code_block,
        )

    def to_dict(self) -> dict:
        return {
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "class_func": self.class_func,
            "docstring": self.docstring,
            "code_block": self.code_block,
        }

    def __str__(self) -> str:
        return (
            f"{self.file_path}:{self.start_line}-{self.end_line}\t{self.class_func}\n"
        )


class Issue:
    issue_id: str
    title: str
    description: str
    Blocks: List[Block]

    def __init__(
        self, issue_id: str, title: str, description: str, blocks: List[Block]
    ):
        self.issue_id = issue_id
        self.title = title
        self.description = description
        self.blocks = blocks

    def to_dict(self) -> dict:
        return {
            "issue_id": self.issue_id,
            "title": self.title,
            "description": self.description,
            "blocks": [block.to_dict() for block in self.blocks],
        }


def is_test_file(file_path: str) -> bool:
    """Check if a file is a test file."""
    test_indicators = ["test", "Test", "TEST"]
    return any(indicator in file_path for indicator in test_indicators)


def parse_diff_file(diff_path: Path, git_repo_path: str) -> List[Block]:
    """
    Parse a diff file and extract file paths with their changed line numbers.
    Returns list of Blocks.
    """
    with open(diff_path, "r") as f:
        content = f.read()

    results = []
    current_file = None
    current_blocks = []

    if content is None:
        print("none")
    lines = content.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # Match file path: diff --git a/path b/path
        file_match = re.match(r"^diff --git a/(.+?) b/(.+?)$", line)
        if file_match:
            # Save previous file's results
            if current_blocks:
                results.extend(current_blocks)

            # Start new file
            current_file = file_match.group(2)  # Use 'b' path (new version)

            current_blocks = []
            i += 1
            continue

        # Match hunk header: @@ -old_start,old_count +new_start,new_count @@
        hunk_match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match and current_file:
            new_start = int(hunk_match.group(1))
            new_end = (
                int(hunk_match.group(1)) + int(hunk_match.group(2))
                if hunk_match.group(2)
                else new_start
            )
            current_blocks.append(
                Block(
                    os.path.join(git_repo_path, current_file),
                    new_start,
                    new_end,
                    None,
                    None,
                    None,
                )
            )

        i += 1

    # Save last file's results
    if current_file and current_blocks:
        results.extend(current_blocks)

    return results


def class_function_at_line(file_path: str, target_line: int):
    """Extract class/function name at a given line in a Java file."""
    # Only process Java files
    if not file_path.endswith(".java"):
        return None
    try:
        with open(file_path, "r") as f:
            java_source = f.read()
        tree = javalang.parse.parse(java_source)
    except Exception as e:
        # javalang raises JavaSyntaxError on unparseable Java; treat as no class/function
        if type(e).__name__ == "JavaSyntaxError":
            return None
        raise

    current_class = None
    current_method = None

    for path, node in tree:
        if hasattr(node, "position") and node.position:
            line = node.position.line

            if line <= target_line:
                if isinstance(node, javalang.tree.ClassDeclaration):
                    current_class = node.name
                elif isinstance(node, javalang.tree.MethodDeclaration):
                    current_method = node.name

    if current_class and current_method:
        return f"{current_class}.{current_method}"
    elif current_class:
        return current_class
    else:
        return None
    return None


def fqfn_at_line(block: Block):
    """Extract fully qualified function name at a given line in a file."""
    if block.class_func is None:
        block.class_func = class_function_at_line(
            block.file_path, (block.start_line + block.end_line) // 2
        )
    # refine_name expects a string; skip or use fallback when class_func is None (e.g. non-Java)
    if block.class_func is None:
        print(
            f"No class/function name found for {block.file_path} at line {block.start_line}-{block.end_line}"
        )
        return None
    return FQFN_EXTRACTOR.refine_name(
        block.file_path, block.class_func, block.start_line, block.end_line
    )


def get_function_name_from_naive_block(block: Block, language: str) -> str:
    """Get the function name from a naive block."""
    if language == "java":
        return fqfn_at_line(block)


def process_diff_file(
    diff_path: Path, git_repo_path: str, language: str
) -> List[Block]:
    """
    Process a single diff file and return list of (file_path, line_num, class_function) tuples.
    """
    blocks = parse_diff_file(diff_path, git_repo_path)
    results = []

    for block in blocks:
        # Skip test files
        if is_test_file(block.file_path):
            continue
        # block.class_func = get_function_name_from_naive_block(block, language)
        block = Block.from_parsed_block(parse_block_from_dict(block.to_dict()))
        results.append(block)

    return results


def write_blocks_csv(issue_id: str, blocks: List[Block], csv_path: Path):
    """Write blocks to a CSV file."""
    with open(csv_path, "a") as f:
        writer = csv.writer(f)

        for block in blocks:
            writer.writerow(
                [
                    issue_id,
                    block.file_path,
                    block.start_line,
                    block.end_line,
                    block.class_func,
                ]
            )


def generate_prompt(issue: Issue, prompt_file: str):
    """write a prompt for an agent to examine the root cause"""
    # Build blocks string separately to avoid backslash in f-string
    blocks_str = "\n".join([str(block) for block in issue.blocks])

    # prompt = f"""
    # You are a software engineer. Your task is to pick out root cause of the issue from the code changes.
    # The issue is as follows:
    # title: {issue.title}
    # description: {issue.description}
    # The code changes are as follows:
    # {blocks_str}
    # Output the root cause with the file path, line number, and class/function name.
    # """
    prompt = json.dumps(issue.to_dict(), indent=2)
    with open(prompt_file, "w") as f:
        f.write(prompt)
    return prompt


def main(
    diffs_dir: str,
    project: str,
    blocks_csv_path: str,
    git_repo_path: str,
    jira_issues_path: str,
    prompt_dir: str,
    language: str = "java",
):
    """Main function to process all diff files."""
    if not os.path.exists(diffs_dir):
        print(f"Error: Diffs directory not found: {diffs_dir}")
        sys.exit(1)

    if not os.path.exists(git_repo_path):
        print(f"Error: Git repository path not found: {git_repo_path}")
        sys.exit(1)

    # Get all diff files
    diff_files = sorted(Path(diffs_dir).glob("*.diff"))

    if not diff_files:
        print(f"No diff files found in {diffs_dir}")
        return

    print(f"Processing {len(diff_files)} diff files...\n")

    all_results = []

    for diff_file in tqdm.tqdm(diff_files):
        issue_id = diff_file.stem
        print(f"Processing {issue_id}...", end=" ")

        try:
            commit = json.load(open(jira_issues_path))[issue_id]["commit"]
            # if project == "etcd":
            #     commit = find_commit_for_issue(
            #         issue_id, json_path=jira_issues_path, git_repo_path=git_repo_path
            #     )
            #     if not commit:
            #         print("Commit not found, skipping")
            #         continue
            # else:
            #     commit = find_commit_for_issue(issue_id)
            # if not commit:
            #     print("Commit not found, skipping")
            #     continue
            run(["git", "-C", git_repo_path, "checkout", f"{commit}^"])

            results = process_diff_file(diff_file, git_repo_path, language)
            title = json.load(open(jira_issues_path))[issue_id]["title"]
            description = json.load(open(jira_issues_path))[issue_id]["description"]
            all_results.append(Issue(issue_id, title, description, results))
            print(f"Found {len(results)} change locations")
        except Exception as e:
            msg = str(e).strip()
            err_info = f"{type(e).__name__}: {msg}" if msg else type(e).__name__
            print(f"Error: {err_info}")
            if not msg:
                traceback.print_exc()
            continue

    # Output results
    print("\n" + "=" * 80)
    print("Results:")
    print("=" * 80)

    print(f"\nTotal: {len(all_results)} issues")
    # clear the block csv file
    with open(blocks_csv_path, "w") as f:
        f.write("issue_id,file_path,start_line,end_line,class_func\n")
    for issue in all_results:
        write_blocks_csv(
            issue.issue_id,
            issue.blocks,
            blocks_csv_path,
        )

        os.makedirs(prompt_dir, exist_ok=True)
        generate_prompt(issue, os.path.join(prompt_dir, f"{issue.issue_id}.json"))


# def main2():
#     diff_file = parse_diff_file(DIFFS_DIR / "ZOOKEEPER-2474.diff")
#     for block in diff_file:
#         print(block)


# def main3():
#     import argparse

#     parser = argparse.ArgumentParser()
#     parser.add_argument("--issue_id", type=str, required=True)
#     args = parser.parse_args()
#     issue_id = args.issue_id
#     commit = find_commit_for_issue(issue_id)
#     if not commit:
#         print("Commit not found")
#         return
#     run(["git", "-C", "/mnt/Ubuntu/zookeeper", "checkout", f"{commit}^"])
#     results = process_diff_file(
#         f"/mnt/Ubuntu/llm-ds-bugs/output/diffs/{issue_id}.diff",
#         "/mnt/Ubuntu/zookeeper",
#         "java",
#     )
#     for block in results:
#         print(block)


# def main4():
#     issue_id = "ZOOKEEPER-2383"
#     issue = json.load(open(JIRA_ISSUES_PATH))
#     print(issue[issue_id]["title"])
#     print(issue[issue_id]["description"])


if __name__ == "__main__":
    main()

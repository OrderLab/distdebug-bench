"""
Script to extract and randomly sample issue keys for Apache JIRA projects
Basic usage:
    python utils/issue_selection.py --project <project_name> --sample-size <size>
View all arguments:
    python utils/issue_selection.py --help
"""

import os
import sys

sys.path.append(os.getcwd())

import argparse
import csv
import json
import random
import sys
import time
from typing import Any, Dict, List, Optional

import requests

from dotenv import load_dotenv

ENV = load_dotenv()


class JiraIssueExtractor:
    def __init__(
        self, base_url: str = "https://issues.apache.org/jira", seed: int = None
    ):
        self.base_url = base_url
        self.api_endpoint = f"{base_url}/rest/api/2/search"
        self.seed = seed or int(time.time())
        random.seed(self.seed)

    def build_jql_query(
        self,
        project: str,
        issue_types: List[str] = None,
        statuses: List[str] = None,
        priorities: List[str] = None,
        resolutions: List[str] = None,
        created_after: str = None,
        exclude_components: List[str] = None,
        include_empty_components: bool = True,
        custom_filters: List[str] = None,
    ) -> str:
        """
        Build a JQL query based on the provided criteria.

        Args:
            project: Project key (e.g., 'KAFKA', 'SPARK', 'HADOOP')
            issue_types: List of issue types (e.g., ['Bug', 'Task'])
            statuses: List of statuses (e.g., ['Resolved', 'Closed'])
            priorities: List of priorities (e.g., ['Blocker', 'Critical', 'Major'])
            resolutions: List of resolutions (e.g., ['Fixed'])
            created_after: Date in 'YYYY-MM-DD' format to filter issues created after this date
            exclude_components: List of components to exclude
            include_empty_components: Whether to include issues with empty components
            custom_filters: Additional JQL filters to add

        Returns:
            Complete JQL query string
        """
        query_parts = [f"project = {project}"]

        def format_list(items: List[str]) -> str:
            quoted_items = [f'"{item}"' for item in items]
            return ", ".join(quoted_items)

        def format_inclusion_list(items: List[str], field: str) -> str:
            items_str = format_list(items)
            return f"{field} in ({items_str})"

        if issue_types:
            query_parts.append(format_inclusion_list(issue_types, "issuetype"))
        if statuses:
            query_parts.append(format_inclusion_list(statuses, "status"))
        if priorities:
            query_parts.append(format_inclusion_list(priorities, "priority"))
        if resolutions:
            query_parts.append(format_inclusion_list(resolutions, "resolution"))

        if created_after:
            query_parts.append(f"created >= '{created_after}'")

        if exclude_components:
            components_str = format_list(exclude_components)
            if include_empty_components:
                query_parts.append(
                    f"(component not in ({components_str}) or component is EMPTY)"
                )
            else:
                query_parts.append(f"component not in ({components_str})")

        if custom_filters:
            query_parts.extend(custom_filters)

        return " AND ".join(query_parts)

    def fetch_issues(
        self, jql_query: str, max_results_per_batch: int = 1000
    ) -> List[Dict]:
        """
        Fetch all issues matching the JQL query.

        Args:
            jql_query: JQL query string
            max_results_per_batch: Maximum results per API call

        Returns:
            List of issues
        """
        all_issues = []
        start_at = 0
        batch_num = 1

        print(f"JQL Query: {jql_query}")
        print(f"Fetching issue from {self.base_url}...")

        max_results = 50000
        while start_at < max_results:
            params = {
                "jql": jql_query,
                "fields": "key,description,assignee,reporter,summary,created,priority",
                "startAt": start_at,
                "maxResults": max_results_per_batch,
            }

            print(f"Fetching batch {batch_num} (starting at {start_at})...")

            try:
                response = requests.get(self.api_endpoint, params=params, timeout=30)
                response.raise_for_status()
                data = response.json()

                if "errorMessages" in data:
                    print(f"JIRA API Error: {'; '.join(data['errorMessages'])}")
                    break

                if not data.get("issues"):
                    print("No more results found")
                    break

                issues = data["issues"]
                for issue in issues:
                    all_issues.append(self.parse_issue(issue))

                print(
                    f"Found {len(issues)} keys in batch {batch_num} (total: {len(all_issues)})"
                )

                if len(issues) < max_results_per_batch:
                    print(
                        f"Reached end of results, fetched {len(all_issues)} total issues"
                    )
                    break

                start_at += max_results_per_batch
                batch_num += 1

                time.sleep(0.1)

            except requests.exceptions.RequestException as e:
                print(f"Error fetching data: {e}")
                break
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON response: {e}")
                break

        return all_issues

    def filter_fetched_issues(self, issues: List[Dict]) -> List[Dict]:
        """
        Filters the given issues based on additional criteria that cannot be expressed in JQL.
        Args:
            issues: List of issues fetched from JIRA API
        Returns:
            List of final issues
        """
        final_issues = []
        description_length_filtered_count = 0
        assignee_reporter_filtered_count = 0

        for issue in issues:
            min_description_length = 20
            if issue["description_word_count"] < min_description_length:
                description_length_filtered_count += 1
                continue

            # Should theoretically be able to filter out issues where assignee == reporter in the
            # initial query, but it doesn't work as expected and still returns issues where they are equal.
            if (
                issue["assignee"] is not None
                and issue["reporter"] is not None
                and issue["assignee"] == issue["reporter"]
            ):
                assignee_reporter_filtered_count += 1
                continue

            final_issues.append(issue)

        print(
            f"Filtered out {description_length_filtered_count} issues with description word count < {min_description_length}"
        )
        print(
            f"Filtered out {assignee_reporter_filtered_count} issues where assignee == reporter"
        )
        print(
            f"Filtered out {len(issues) - len(final_issues)} total issues, {len(final_issues)} issues remain"
        )

        return final_issues

    @staticmethod
    def read_issues_from_file(filename: str) -> List[Dict]:
        """
        Reads issues from a file. The file should've been created in a previous run of this script.
        Args:
            filename: Input filename
        Returns:
            List of issues read from the file
        """
        issues = []
        with open(filename, "r") as csvfile:
            reader = csv.DictReader(csvfile, delimiter="\t")
            for row in reader:
                issues.append(row)

        print(f"Read {len(issues)} issues from {filename}")
        return issues

    @staticmethod
    def save_issues_to_file(issues: List[Dict], filename: str) -> None:
        """
        Saves given issues to a file.
        Args:
            issues: List of issues to save
            filename: Output filename
            random_sample: Whether the issues are the final random sample or the full list
        """
        if not issues:
            print("No issues to save")
            return

        fieldnames = [
            "issue_link",
            "key",
            "creation_date",
            "priority",
            "summary",
            "description_word_count",
        ]

        issues_to_write = []
        for issue in issues:
            issue_to_write = {field: issue.get(field, "") for field in fieldnames}
            issues_to_write.append(issue_to_write)

        with open(filename, "w", newline="") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(issues_to_write)

        print(f"Saved {len(issues_to_write)} issues to {filename}")

    def random_sample(self, issues: List, sample_size: int) -> List:
        """Generate a random sample of issues."""
        if sample_size > len(issues):
            print(
                f"Warning: Sample size {sample_size} is larger than total issues {len(issues)}"
            )
            return issues

        print(
            f"Generating random sample of size {sample_size} from {len(issues)} issues with seed {self.seed}"
        )
        return random.sample(issues, sample_size)

    def parse_issue(self, issue: Dict) -> Dict:
        """
        Extracts relevant fields from a JIRA issue object.
        """
        key = issue["key"]
        issue_link = f"{self.base_url}/browse/{key}"
        fields = issue.get("fields", {})
        description = fields.get("description", "") or ""
        description_word_count = len(description.split())
        assignee = JiraIssueExtractor.get_user_key(fields.get("assignee"))
        reporter = JiraIssueExtractor.get_user_key(fields.get("reporter"))
        created = fields.get("created", "") or ""
        # Parse only the date part (YYYY-MM-DD) from the creation date string
        if "T" in created:
            created = created.split("T")[0]
        priority = fields.get("priority", {}) or {}
        priority = priority.get("name", "") or ""
        summary = fields.get("summary", "") or ""

        return {
            "issue_link": issue_link,
            "key": key,
            "creation_date": created,
            "priority": priority,
            "summary": summary,
            "assignee": assignee,
            "reporter": reporter,
            "description_word_count": description_word_count,
            "description": description,
        }

    @staticmethod
    def get_user_key(user) -> str | None:
        """
        Extract key from user object. Returns None if user is None or key is not present.
        """
        return user.get("key") if user else None


def get_project_configs() -> Dict[str, Dict[str, Any]]:
    common_config = {
        "issue_types": ["Bug"],
        "statuses": ["Resolved", "Closed"],
        "resolutions": ["Fixed"],
        "priorities": ["Blocker", "Critical", "Major"],
        "created_after": "2016-01-01",
    }

    return {
        "kafka": {
            "project": "KAFKA",
            **common_config,
            "exclude_components": [
                "admin",
                "build",
                "config",
                "docker",
                "docs",
                "documentation",
                "packaging",
                "release",
                "streams-test-utils",
                "system tests",
                "unit tests",
                "website",
            ],
        },
        "zookeeper": {
            "project": "ZOOKEEPER",
            **common_config,
            "exclude_components": [
                "audit",
                "build",
                "build-infrastructure",
                "contrib",
                "contrib-bookkeeper",
                "contrib-fatjar",
                "contrib-hedwig",
                "contrib-zkfuse",
                "documentation",
                "license",
                "recipes",
                "scripts",
                "tests",
                "tools",
            ],
        },
        "hdfs": {
            "project": "HDFS",
            **common_config,
            "exclude_components": [
                "benchmarks",
                "build",
                "configuration",
                "contrib/hdfsproxy",
                "contrib/raid",
                "documentation",
                "examples",
                "scripts",
                "test",
                "tools",
            ],
        },
        "hbase": {
            "project": "HBASE",
            **common_config,
            "exclude_components": [
                "Admin",
                "build",
                "community",
                "documentation",
                "flakies",
                "dependabot",
                "integration tests",
                "findbugs",
                "scripts",
                "test",
                "tooling",
                "website",
            ],
        },
        "ignite": {
            "project": "IGNITE",
            **common_config,
            "exclude_components": [
                "build",
                "builds/deliveries ai3",
                "configuration",
                "documentation",
                "documentation ai3",
                "examples",
                "packaging ai3",
                "site",
                "website",
            ],
        },
        "etcd": {
            "project": "ETCD",
            **common_config,
            "exclude_components": [
                "build",
                "documentation",
                "scripts",
                "test",
                "tools",
            ],
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract issue keys from Apache JIRA projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
            Examples:
            # Specify project
            python3 jira_extractor.py --project kafka
            """,
    )
    project_configs = get_project_configs()
    parser.add_argument(
        "--project",
        choices=project_configs.keys(),
        required=True,
        help="Use predefined configuration for the specified project",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Generate random sample of specified size",
    )
    parser.add_argument(
        "--filtered-issues-output",
        default=None,
        help="Filtered issues output filename (default: {project}_issues.csv)",
    )
    parser.add_argument(
        "--sample-output",
        default=None,
        help="Sample output filename (default: {project}_sample_{size}.csv)",
    )
    parser.add_argument(
        "--base-url", default="https://issues.apache.org/jira", help="JIRA base URL"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Batch size for API calls, cannot be higher than 1000",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for sampling (default: current timestamp)",
    )
    parser.add_argument(
        "--issues-file",
        type=str,
        default=None,
        help="If specified, randomly sample issues from the provided file, which should have been created in a previous run of this script",
    )

    args = parser.parse_args()
    if args.batch_size > 1000:
        print("Error: batch-size cannot be higher than 1000")
        sys.exit(1)

    if args.issues_file and not os.path.isfile(args.issues_file):
        print(f"Error: File {args.issues_file} does not exist")
        sys.exit(1)

    return args


def main():
    args = parse_args()

    filtered_issues = []
    issues_path_prefix = os.path.join(ENV.out_path, "issues")
    os.makedirs(issues_path_prefix, exist_ok=True)
    extractor = JiraIssueExtractor(args.base_url, args.seed)
    if not args.issues_file:
        project_configs = get_project_configs()
        config = project_configs[args.project].copy()
        jql_query = extractor.build_jql_query(**config)
        issues = extractor.fetch_issues(jql_query, args.batch_size)
        filtered_issues = extractor.filter_fetched_issues(issues)
        issues_file = args.filtered_issues_output or os.path.join(
            issues_path_prefix, f"{args.project}_issues.csv"
        )
        JiraIssueExtractor.save_issues_to_file(filtered_issues, issues_file)

        if not filtered_issues:
            print("No issues found matching the criteria")
            sys.exit(1)

        print(
            f"Found {len(filtered_issues)} issues matching query and additional criteria"
        )
    else:
        filtered_issues = JiraIssueExtractor.read_issues_from_file(args.issues_file)
        print(f"Read {len(filtered_issues)} issues from {args.issues_file}")

    sample_keys = extractor.random_sample(filtered_issues, args.sample_size)
    sample_file = args.sample_output or os.path.join(
        issues_path_prefix, f"{args.project}_sampled_issues.csv"
    )
    JiraIssueExtractor.save_issues_to_file(sample_keys, sample_file)

    print(f"\nSummary:")
    print(f"Project: {args.project}")
    if not args.issues_file:
        print(f"Query: {jql_query}")
        print(f"Saved {len(filtered_issues)} total issues to {issues_file}")
    else:
        print(f"Read {len(filtered_issues)} issues from {args.issues_file}")
    print(f"Randomly sampled {len(sample_keys)} issues to {sample_file}")


if __name__ == "__main__":
    main()

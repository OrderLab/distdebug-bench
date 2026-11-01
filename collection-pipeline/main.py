import json
import os
import sys
import time

import utils.extract_diff_context
import utils.issue_selection
import utils.jira_scraper
import utils.mydiff
from utils.clone_system_repos import clone_repo
from dotenv import load_dotenv
from utils.generate_diff_files import generate_diff_files

ENV = load_dotenv()


def get_url(project: str):
    if project == "kafka":
        return "https://github.com/apache/kafka.git"
    elif project == "zookeeper":
        return "https://github.com/apache/zookeeper.git"
    elif project == "hdfs":
        return "https://github.com/apache/hadoop.git"
    elif project == "hbase":
        return "https://github.com/apache/hbase.git"
    elif project == "ignite":
        return "https://github.com/apache/ignite.git"
    elif project == "ignite3":
        return "https://github.com/apache/ignite.git"
    elif project == "etcd":
        return "https://github.com/etcd-io/etcd.git"
    else:
        raise ValueError(f"Project {project} not supported")


def main():
    args = utils.issue_selection.parse_args()
    # GIT_REPO_PATH = ENV.test_systems_root_path + "/" + args.project
    clone_repo(args.project, get_url(args.project))
    issues_path_prefix = os.path.join(os.getenv("OUT_PATH"), "issues")
    # sample_file = args.sample_output or os.path.join(issues_path_prefix, f"{args.project}_sampled_issues.csv")
    # Read the sample file and get the issue URLs
    # jira_scraper.main(csv_path=sample_file, json_path=os.path.join(issues_path_prefix, f"{args.project}_jira_issues.json"))

    generate_diff_files(
        args.project,
        os.path.join(os.getenv("TEST_SYSTEMS_ROOT_PATH"), args.project),
        os.path.join(os.getenv("OUT_PATH"), "diffs", args.project),
        os.getenv("GITHUB_TOKEN"),
        json_path=os.path.join(issues_path_prefix, f"{args.project}.json"),
        project_name=args.project,
    )
    utils.extract_diff_context.main(
        diffs_dir=os.path.join(os.getenv("OUT_PATH"), "diffs", args.project),
        project=args.project,
        blocks_csv_path=os.path.join(
            os.getenv("OUT_PATH"), "issues", f"{args.project}_blocks.csv"
        ),
        git_repo_path=os.path.join(os.getenv("TEST_SYSTEMS_ROOT_PATH"), args.project),
        jira_issues_path=os.path.join(issues_path_prefix, f"{args.project}.json"),
        prompt_dir=os.path.join(os.getenv("OUT_PATH"), "prompts", args.project),
    )
    print("Final bug pipeline completed")


if __name__ == "__main__":
    main()

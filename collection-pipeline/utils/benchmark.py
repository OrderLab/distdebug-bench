import csv
import os
from textwrap import dedent
from typing import Dict, List, Optional, Set, Tuple

DNSJAVA = "dnsjava"
ZOOKEEPER = "zookeeper"
KAFKA = "kafka"
IGNITE = "ignite"
IGNITE3 = "ignite3"
HDFS = "hdfs"
HBASE = "hbase"
CASSANDRA = "cassandra"
MAPREDUCE = "mapreduce"

def from_conventional_name(conventional_name: str) -> str:
    """
    Converts a conventional name to the corresponding system name.
    """
    trans: Dict[str, str] = {
        "DNS-Java": DNSJAVA,
        "ZooKeeper": ZOOKEEPER,
        "Kafka": KAFKA,
        "Ignite": IGNITE,
        "Ignite-3": IGNITE3,
        "HDFS": HDFS,
        "HBase": HBASE,
        "Cassandra": CASSANDRA,
        "MapReduce": MAPREDUCE
    }
    if conventional_name not in trans:
        raise ValueError(f"Unknown name: {conventional_name}")
    return trans[conventional_name]

def validate_benchmark(bench: List[Dict]) -> Set[str]:
    seen_reports = set()
    for row in bench:
        if not len(row['System']):
            continue
        report_name = f"{from_conventional_name(row['System'])}-{row['ID']}"
        if report_name in seen_reports:
            raise ValueError(f"Duplicate bug report found: {report_name}")
        seen_reports.add(report_name)
    return seen_reports

def validate_blocks(seen_reports: Set[str], blocks: List[Dict]) -> None:
    seen_blocks = set()
    for row in blocks:
        if not len(row['System']):
            continue
        report_name = f"{from_conventional_name(row['System'])}-{row['ID']}"
        seen_blocks.add(report_name)
    
    missing_reports = seen_blocks - seen_reports
    if missing_reports:
        raise ValueError(f"Missing bug report metadata for: {missing_reports}")
    missing_blocks = seen_reports - seen_blocks
    if missing_blocks:
        raise ValueError(f"Missing buggy blocks for: {missing_blocks}")

def validate_dataset(bench: List[Dict], blocks: Optional[List[Dict]]) -> None:
    """
    Validates the dataset by checking for duplicate bug reports and ensuring that all buggy blocks
    have corresponding bug report metadata.
    """
    seen_reports = validate_benchmark(bench)
    if blocks is not None:
        validate_blocks(seen_reports, blocks)

def read_csv(filename: str) -> List[Dict]:
    with open(filename) as f:
        reader = csv.DictReader(f)
        return list(reader)

def read_systems(systems_csv: List[Dict]) -> List[Dict]:
    systems = {}
    for row in systems_csv:
        system_name = from_conventional_name(row["Name"])
        systems[system_name] = {
            "url": row["URL"],
            "project_prompt": row["Project Prompt"]
        }
    return systems

def read_report(benchmark_csv: List[Dict]) -> List[Dict]:
    bug_reports = {}
    for row in benchmark_csv:
        report_name = f"{from_conventional_name(row['System'])}-{row['ID']}"
        bug_reports[report_name] = {
            "project_name": from_conventional_name(row['System']),
            "branch": row["Branch"],
            "title": row["Title"],
            "description": row["Description"],
            "log_file": ""
        }
    return bug_reports

def read_metadata(benchmark_csv: List[Dict]) -> List[Dict]:
    bug_metadata = {}
    for row in benchmark_csv:
        report_name = f"{from_conventional_name(row['System'])}-{row['ID']}"
        bug_metadata[report_name] = {
            "severity": row["Severity"],
            "root_cause_type": row["Root Cause Type"],
            "bug_complexity": row["Bug Complexity"],
            "distributed": row["Distributed?"],
        }
    return bug_metadata

def read_correct_diagnoses(blocks_csv: List[Dict]) -> List[Dict]:
    correct_bug_diagnoses = {}
    for row in blocks_csv:
        report_name = f"{from_conventional_name(row['System'])}-{row['ID']}"
        buggy_block = {
            "file": row["Buggy File"],
            "function": row["Buggy Function"],
            "start_line": int(row["Start Line"]),
            "end_line": int(row["End Line"]),
        }
        if report_name not in correct_bug_diagnoses:
            correct_bug_diagnoses[report_name] = {
                "buggy_blocks": [buggy_block],
                "bug_cause_description": ""
            }
        else:
            correct_bug_diagnoses[report_name]["buggy_blocks"].append(buggy_block)
    return correct_bug_diagnoses
    
def read_benchmark_csv(benchmark_dir: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    bench_path = os.path.join(benchmark_dir, 'benchmark', 'benchmark.csv')
    blocks_path = os.path.join(benchmark_dir, 'benchmark', 'blocks.csv')
    systems_path = os.path.join(benchmark_dir, 'systems.csv')

    bench = read_csv(bench_path)
    blocks = read_csv(blocks_path)
    systems_list = read_csv(systems_path)
    validate_dataset(bench, blocks)

    # Remove all empty rows
    bench = [row for row in bench if len(row['System'])]
    blocks = [row for row in blocks if len(row['System'])]  
    systems_list = [row for row in systems_list if len(row['Name'])]

    return bench, blocks, systems_list

def read_benchmark(benchmark_dir: str) -> Tuple[Dict, Dict, Dict, Dict]:
    """
    Reads benchmark data from CSV files. 
    Args:
        benchmark_dir (str): The directory containing the dataset.
    Returns:
        Tuple containing:
            - bug_reports (Dict): A dictionary mapping report names to their bug report data.
            - correct_bug_diagnoses (Dict): A dictionary mapping report names to their correct diagnoses
            - bug_metadata (Dict): A dictionary mapping report names to their additional metadata.
            - systems (Dict): A dictionary mapping system names to their metadata.
    """
    bench, blocks, systems_list = read_benchmark_csv(benchmark_dir)
    
    bug_reports = read_report(bench)
    correct_bug_diagnoses = read_correct_diagnoses(blocks)
    bug_metadata = read_metadata(bench)
    systems = read_systems(systems_list)

    return bug_reports, correct_bug_diagnoses, bug_metadata, systems

def read_open_issues_csv(benchmark_dir: str) -> Tuple[List[Dict], List[Dict]]:
    open_issues_path = os.path.join(benchmark_dir, 'open_issues', 'open_issues.csv')
    systems_path = os.path.join(benchmark_dir, 'systems.csv')

    issues = read_csv(open_issues_path)
    systems_list = read_csv(systems_path)
    validate_dataset(issues, None)
    
    # Remove all empty rows
    issues = [row for row in issues if len(row['System'])]
    systems_list = [row for row in systems_list if len(row['Name'])]
    
    return issues, systems_list

def read_open_issues(benchmark_dir: str) -> Tuple[Dict, Dict, Dict]:
    """
    Reads open issues data from CSV files. 
    Args:
        benchmark_dir (str): The directory containing the dataset.
    Returns:
        Tuple containing:
            - open_issues_reports (Dict): A dictionary mapping issue names to their issue data.
            - issue_metadata (Dict): A dictionary mapping issue names to their additional metadata.
            - systems (Dict): A dictionary mapping system names to their metadata.
    """
    issues, systems_list = read_open_issues_csv(benchmark_dir)
    
    open_issues_reports = read_report(issues)
    issue_metadata = read_metadata(issues)
    systems = read_systems(systems_list)

    return open_issues_reports, issue_metadata, systems

def read_zhang_benchmark_csv(benchmark_dir: str) -> Tuple[List[Dict], List[Dict]]:
    zhang_benchmark_path = os.path.join(benchmark_dir, 'zhang_benchmark', 'zhang_benchmark.csv')
    zhang_blocks_path = os.path.join(benchmark_dir, 'zhang_benchmark', 'zhang_blocks.csv')
    systems_path = os.path.join(benchmark_dir, 'systems.csv')

    bench = read_csv(zhang_benchmark_path)
    blocks = read_csv(zhang_blocks_path)
    systems_list = read_csv(systems_path)
    validate_dataset(bench, blocks)

    # Remove all empty rows
    bench = [row for row in bench if len(row['System'])]
    blocks = [row for row in blocks if len(row['System'])]
    systems_list = [row for row in systems_list if len(row['Name'])]

    return bench, blocks, systems_list

def read_zhang_benchmark(benchmark_dir: str) -> Tuple[Dict, Dict, Dict, Dict]:
    """
    Reads Zhang benchmark data from CSV files. 
    Args:
        benchmark_dir (str): The directory containing the dataset.
    Returns:
        Tuple containing:
            - bug_reports_list (Dict): A dictionary mapping report names to their bug report data.
            - correct_bug_diagnoses (Dict): A dictionary mapping report names to their correct diagnoses
            - bug_metadata (Dict): A dictionary mapping report names to their additional metadata.
            - systems (Dict): A dictionary mapping system names to their metadata.
    """
    bench, blocks, systems_list = read_zhang_benchmark_csv(benchmark_dir)
    
    bug_reports_list = read_report(bench)
    correct_bug_diagnoses = read_correct_diagnoses(blocks)
    bug_metadata = read_metadata(bench)
    systems = read_systems(systems_list)

    return bug_reports_list, correct_bug_diagnoses, bug_metadata, systems
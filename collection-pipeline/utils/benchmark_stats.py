import os
import sys

sys.path.append(os.getcwd())

import json
from datetime import datetime
from enum import Enum
from functools import partial
from statistics import mean, median, stdev
from typing import Dict, List

from utils.benchmark import read_benchmark_csv
from utils.env import load_env

ENV = load_env()

DECIMAL_PLACES = 2

class StringEnum(Enum):
    """Base class for enums that can be parsed from strings."""
    @classmethod
    def from_string(cls, s: str):
        for member in cls:
            if member.value == s:
                return member
        raise ValueError(f"Invalid value '{s}' for enum {cls.__name__}")

class Severity(StringEnum):
    BLOCKER = 'Blocker'
    CRITICAL = 'Critical'
    MAJOR = 'Major'

class RootCauseType(StringEnum):
    CONCURRENCY = 'Concurrency'
    ERROR_HANDLING = 'Error handling'
    COMMUNICATION = 'Communication'
    RESOURCE_LEAK = 'Resource leak'
    PERFORMANCE = 'Performance'
    LOGIC = 'Logic'
    SECURITY = 'Security'
    OTHER = 'Other'

class BugComplexity(StringEnum):
    EASY = 'Easy'
    MEDIUM = 'Medium'
    HARD = 'Hard'

class EnumCharacteristics(StringEnum):
    SEVERITY = 'Severity'
    ROOT_CAUSE = 'Root Cause Type'
    BUG_COMPLEXITY = 'Bug Complexity'

CHAR_TO_ENUM = {
    EnumCharacteristics.SEVERITY: Severity,
    EnumCharacteristics.ROOT_CAUSE: RootCauseType,
    EnumCharacteristics.BUG_COMPLEXITY: BugComplexity
}

class BoolCharacteristics(StringEnum):
    DISTRIBUTED = 'Distributed?'
    CONTAINS_ANALYSIS = 'Contains Analysis?'
    ANALYSIS_REMOVED = 'Analysis Removed?'
    CONTAINS_LOGS = 'Contains Logs?'
    CONTAINS_STACK_TRACES = 'Contains Stack Traces?'

TOTAL = 'total'

def enum_keys_to_str(obj):
    if isinstance(obj, dict):
        return {str(k) if isinstance(k, Enum) else k: enum_keys_to_str(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [enum_keys_to_str(i) for i in obj]
    else:
        return obj

def get_system_name_from_row(system_row: Dict) -> str:
    return system_row['Name']

def count_issues(
    benchmark_stats: Dict,
    issues: List[Dict],
    systems: List[Dict]
) -> Dict:
    benchmark_stats['Total Issues'] = {TOTAL: len(issues)}
    for system in systems:
        system_name = get_system_name_from_row(system)
        system_issues = [issue for issue in issues if issue['System'] == system_name]
        benchmark_stats['Total Issues'][system_name] = len(system_issues)
    return benchmark_stats

def count_issue_characteristics(
    benchmark_stats: Dict, 
    issues: List[Dict], 
    systems: List[Dict]
) -> Dict:
    def init_enum_field(field_name: str, enum_cls: Enum):
        benchmark_stats[field_name] = {}
        for type in enum_cls:
            benchmark_stats[field_name][type.value] = {}
            benchmark_stats[field_name][type.value][TOTAL] = 0
            for system in systems:
                benchmark_stats[field_name][type.value][get_system_name_from_row(system)] = 0

    for char in EnumCharacteristics:
        init_enum_field(char.value, CHAR_TO_ENUM[char])

    def init_bool_field(field_name: str):
        benchmark_stats[field_name] = {}
        benchmark_stats[field_name][TOTAL] = 0
        for system in systems:
            benchmark_stats[field_name][get_system_name_from_row(system)] = 0

    for char in BoolCharacteristics:
        init_bool_field(char.value)

    for issue in issues:
        system_name = issue['System']

        def increment_enum_field(field_name: str, enum_cls: Enum):
            field_value = issue[field_name]
            enum_value = enum_cls.from_string(field_value).value
            benchmark_stats[field_name][enum_value][TOTAL] += 1
            benchmark_stats[field_name][enum_value][system_name] += 1

        for char in EnumCharacteristics:
            increment_enum_field(char.value, CHAR_TO_ENUM[char])

        def increment_bool_field(field_name: str):
            if issue[field_name] == 'Yes':
                benchmark_stats[field_name][TOTAL] += 1
                benchmark_stats[field_name][system_name] += 1

        for char in BoolCharacteristics:
            increment_bool_field(char.value)

    return benchmark_stats

def calculate_summary_stats(
    benchmark_stats: Dict,
    issues: List[Dict],
    data_name: str,
    value_fn: callable
) -> Dict:
    """
    Calculates summary statistics of the given issues using the provided value function
    and adds them to benchmark_stats.

    value_fn should take an issue and return a numeric value.
    """
    if (len(issues) == 0):
        raise ValueError(f"No issues given to calculate {data_name} statistics")

    values = [value_fn(issue) for issue in issues]

    benchmark_stats[data_name] = {
        'Mean': round(mean(values), DECIMAL_PLACES),
        'Median': round(median(values), DECIMAL_PLACES),
        'Std Dev': round(stdev(values), DECIMAL_PLACES) if len(values) > 1 else 0.0,
        'Min': round(min(values), DECIMAL_PLACES),
        'Max': round(max(values), DECIMAL_PLACES)
    }

    return benchmark_stats

def calculate_description_len_stats(benchmark_stats: Dict, issues: List[Dict]) -> Dict:
    """
    Calculates description length (in words) statistics of the given issues and adds them
    to benchmark_stats. 
    """
    def num_words_in_description(issue: Dict) -> int:
        description_len = len(issue['Description'].split())
        if description_len <= 0:
            raise ValueError(f"Issue {issue['ID']} in system {issue['System']} has a non-positive description length")
        return description_len

    benchmark_stats = calculate_summary_stats(
        benchmark_stats, 
        issues, 
        'Description Length', 
        num_words_in_description
    )

    return benchmark_stats

def calculate_issue_age_stats(benchmark_stats: Dict, issues: List[Dict]) -> Dict:
    """
    Calculates issue age (in years) statistics of the given issues and adds them
    to benchmark_stats. 
    """
    def issue_age_in_years(issue: Dict) -> float:
        creation_date = datetime.strptime(issue['Issue Creation Date'], '%Y-%m-%d')
        current_date = datetime.now()
        age_days = (current_date - creation_date).days
        if age_days <= 0:
            raise ValueError(f"Issue {issue['ID']} in system {issue['System']} has a non-positive age in days: {age_days}")
        return age_days / 365.25  # Approximate year length accounting for leap years

    benchmark_stats = calculate_summary_stats(
        benchmark_stats, 
        issues, 
        'Issue Age', 
        issue_age_in_years
    )

    return benchmark_stats

def calculate_diagnosis_len_stats(
    benchmark_stats: Dict,
    issues: List[Dict],
    blocks: List[Dict]
) -> Dict:
    """
    Calculates diagnosis length (in lines of code) statistics of the given issues and adds them
    to benchmark_stats. 
    """
    def num_lines_in_diagnosis(issue: Dict, blocks: List[Dict]) -> int:
        system_name = issue['System']
        issue_id = issue['ID']
        diagnosis_blocks = [
            block for block in blocks 
            if block['System'] == system_name and block['ID'] == issue_id
        ]

        if len(diagnosis_blocks) == 0:
            raise ValueError(f"No buggy blocks found for issue {issue_id} in system {system_name}")
        
        block_lens = [(int(block['End Line']) - int(block['Start Line'])) for block in diagnosis_blocks]
        for length in block_lens:
            if length <= 0:
                raise ValueError(f"Issue {issue_id} in system {system_name} has a non-positive block length")
        return sum(block_lens)

    benchmark_stats = calculate_summary_stats(
        benchmark_stats, 
        issues, 
        'Diagnosis Length', 
        partial(num_lines_in_diagnosis, blocks=blocks)
    )

    return benchmark_stats

def generate_benchmark_stats(
    issues: List[Dict],
    blocks: List[Dict],
    systems: List[Dict]
) -> Dict:
    benchmark_stats = {}
    benchmark_stats = count_issues(benchmark_stats, issues, systems)
    benchmark_stats = count_issue_characteristics(benchmark_stats, issues, systems)
    benchmark_stats = calculate_description_len_stats(benchmark_stats, issues)
    benchmark_stats = calculate_issue_age_stats(benchmark_stats, issues)
    benchmark_stats = calculate_diagnosis_len_stats(benchmark_stats, issues, blocks)

    return benchmark_stats

def generate_per_system_table(
    system_names: List[str], 
    caption: str,
    data_category: str,
    data_names: List[str],
    data_values: Dict[str, Dict[str, int]]
) -> str:
    # characteristic + systems + total, whitespace before total for visual separation
    col_spec = 'l' + ('r' * (len(system_names)) + '@{\\hskip 24pt}r')
    lines = [
        r'\begin{table*}[ht]',
        r'\centering',
        f'\\caption{{{caption}}}',
        f'\\label{{tab:benchmark_{data_category.lower().replace(" ", "_")}}}',
        f'\\begin{{tabular}}{{{col_spec}}}',
        r'\toprule',
    ]
    
    # Header row
    header = f'\\textbf{{{data_category}}}'
    for system in system_names:
        header += f' & \\textbf{{{system}}}'
    header += f' & \\textbf{{Total}}'
    header += r' \\'
    lines.append(header)
    lines.append(r'\midrule')
    
    # Data rows for each enum value
    for data_name in data_names:
        row = f'\\textbf{{{data_name}}}'
        for system in system_names:
            count = data_values[data_name][system]
            row += f' & {count}'
        row += f' & {data_values[data_name][TOTAL]}'
        row += r' \\'
        lines.append(row)
    
    # Footer
    lines.append(r'\bottomrule')
    lines.append(r'\end{tabular}')
    lines.append(r'\end{table*}')

    return '\n'.join(lines)

def generate_summary_table(
    caption: str,
    data_category: str, 
    data_names: List[str], 
    data_values: Dict[str, Dict[str, float]]
) -> str:
    # top row: Mean, Median, Stdev, Min, Max
    included_stats = data_values[data_names[0]].keys() 
    num_stats = len(included_stats)
    col_spec = 'l' + ('r' * num_stats)

    lines = [
        r'\begin{table*}[ht]',
        r'\centering',
        f'\\caption{{{caption}}}',
        f'\\label{{tab:benchmark_{data_category.lower().replace(" ", "_")}}}',
        f'\\begin{{tabular}}{{{col_spec}}}',
        r'\toprule',
    ]

    # Header row
    header = f'\\textbf{{{data_category}}}'
    for stat in included_stats:
        header += f' & \\textbf{{{stat}}}'
    header += r' \\'
    lines.append(header)
    lines.append(r'\midrule')

    # Row for each data name
    for data_name in data_names:
        row = f'\\textbf{{{data_name}}}'
        for stat in included_stats:
            value = data_values[data_name][stat]
            row += f' & {value}'
        row += r' \\'
        lines.append(row)

    lines.append(r'\bottomrule')
    lines.append(r'\end{tabular}')
    lines.append(r'\end{table*}')

    return '\n'.join(lines)

def combine_ignite_counts(stats: Dict[str, Dict[str, int]]) -> Dict[str, Dict[str, int]]:
    combined = {}
    for data_type, values in stats.items():
        combined[data_type] = {}
        combined[data_type]['Ignite'] = values['Ignite'] + values['Ignite-3']
        for system, count in values.items():
            if system not in ['Ignite', 'Ignite-3']:
                combined[data_type][system] = count
        combined[data_type][TOTAL] = values[TOTAL]
    return combined

def generate_tables(benchmark_stats: Dict, systems: List[Dict]) -> Dict[str, str]:
    system_names = [get_system_name_from_row(system) for system in systems]
    system_names.sort()

    # Remove systems with 0 issues
    system_names = [name for name in system_names if benchmark_stats['Total Issues'][name] > 0]

    # Treat Ignite and Ignite-3 as the same system in tables
    if 'Ignite' in system_names and 'Ignite-3' in system_names:
        system_names.remove('Ignite-3')
    
    tables = {}
    
    # Generate tables for each enum characteristic
    for char in EnumCharacteristics:
        char_data = combine_ignite_counts(benchmark_stats[char.value])
        enum_cls = CHAR_TO_ENUM[char]
        enum_values = [e.value for e in enum_cls]
        
        caption = f'Distribution of {char.value} across systems.'
        table = generate_per_system_table(
            system_names, 
            caption, 
            char.value, 
            enum_values, 
            char_data
        )
        tables[char.value] = table

    # Generate single table for all boolean characteristics
    bool_char_data = combine_ignite_counts({char.value: benchmark_stats[char.value] for char in BoolCharacteristics})
    bool_char_names = [char.value for char in BoolCharacteristics]
    caption = 'Distribution of boolean characteristics across systems.'
    bool_table = generate_per_system_table(system_names, caption, 'Boolean Characteristics', bool_char_names, bool_char_data)
    tables['Boolean Characteristics'] = bool_table

    # Generate table for description length, issue age, and diagnosis length
    data_category = 'Issue Statistics'
    data_names = ['Description Length', 'Issue Age', 'Diagnosis Length']
    data_values = {name: benchmark_stats[name] for name in data_names}
    caption = 'Summary statistics for issue description length (in words), issue age (in years), and diagnosis length (in lines of code).'
    summary_table = generate_summary_table(
        caption, 
        data_category, 
        data_names, 
        data_values
    )
    tables['Issue Statistics'] = summary_table
    
    return tables

def write_stats_to_file(benchmark_stats: Dict, dir: str) -> None:
    stats_str_keys = enum_keys_to_str(benchmark_stats)
    with open(os.path.join(dir, 'benchmark_stats.json'), 'w') as f:
        f.write(json.dumps(stats_str_keys, indent=2))

def write_tables_to_files(tables: Dict[str, str], dir: str) -> None:
    for char_name, table in tables.items():
        file_name = char_name.lower().replace(" ", "_") + '.tex'
        with open(os.path.join(dir, file_name), 'w') as f:
            f.write(table)

if __name__ == '__main__':
    issues, blocks, systems = read_benchmark_csv(ENV.benchmark_path)
    stats = generate_benchmark_stats(issues, blocks, systems)

    benchmark_stats_dir_path = os.path.join(ENV.out_path, 'benchmark_stats')
    os.makedirs(benchmark_stats_dir_path, exist_ok=True)
    write_stats_to_file(stats, benchmark_stats_dir_path)

    tables = generate_tables(stats, systems)
    write_tables_to_files(tables, benchmark_stats_dir_path)
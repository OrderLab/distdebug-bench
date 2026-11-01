import os
import sys

sys.path.append(os.getcwd())
import csv
import json

from utils.env import load_env

ENV = load_env()

def convert_csv_to_json_file(dir_path, keyword):
    """Convert a single CSV file to JSON format."""
    csv_path = os.path.join(dir_path, f"{keyword}.csv")
    json_path = os.path.join(dir_path, f"{keyword}.json")

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with open(json_path, 'w') as f:
        json.dump(rows, f, indent=2)
    
    print(f"Converted {csv_path} to {json_path}")

def convert_csv_to_json_in_dir(root_path):
    """Convert all CSV files in the specified directory to JSON format."""
    for dir_path, _, files in os.walk(root_path):
        for file in files:
            if file.endswith('.csv'):
                keyword = file[:-4]
                convert_csv_to_json_file(dir_path, keyword)

if __name__ == '__main__':
    convert_csv_to_json_in_dir(ENV.benchmark_path)
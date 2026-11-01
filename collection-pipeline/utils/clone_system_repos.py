import subprocess
import sys
from os import getcwd, path

sys.path.append(getcwd())
from utils.benchmark import read_benchmark
from dotenv import load_dotenv
import os

ENV = load_dotenv()


def clone_repo(name: str, url: str):
    # Clones the repository only if it doesn't already exist
    repo_path = path.join(os.getenv("TEST_SYSTEMS_ROOT_PATH"), name)
    if path.exists(repo_path):
        print(f"Repository {name} already exists at {repo_path}")
        return
    try:
        print(f"Cloning repository {name} from {url}...")
        result = subprocess.run(
            ["git", "clone", url, repo_path], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise Exception(result.stderr)
        print(f"Cloned repository {name} to {repo_path}")
    except Exception as e:
        print(f"Error cloning repository {name}: {e}")


if __name__ == "__main__":
    _, _, _, systems = read_benchmark(os.getenv("BENCHMARK_PATH"))
    for name, value in systems.items():
        clone_repo(name, value["url"])
    print("All repositories cloned successfully.")

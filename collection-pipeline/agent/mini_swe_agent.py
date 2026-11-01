"""This is the simplest possible example of how to use mini-SWE-agent with python bindings.
For a more complete example, see mini.py
"""

import logging
import os
from pathlib import Path
import json
import typer
import yaml
from dotenv import load_dotenv
import subprocess
from minisweagent import package_dir
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_model import LitellmModel

app = typer.Typer()
load_dotenv()


@app.command()
def main(
    # task: str = typer.Option(
    #     ...,
    #     "-t",
    #     "--task",
    #     help="Task/problem statement",
    #     show_default=False,
    #     prompt=True,
    # ),
    model_name: str = typer.Option(
        os.getenv("MSWEA_MODEL_NAME"),
        "-m",
        "--model",
        help="Model name (defaults to MSWEA_MODEL_NAME env var)",
        prompt="What model do you want to use?",
    ),
    issue_id: str = typer.Option(
        ...,
        "-i",
        "--issue-id",
        help="Issue ID (e.g. ZOOKEEPER-2581)",
        show_default=False,
    ),
    project_name: str = typer.Option(
        ...,
        "-p",
        "--project-name",
        help="Project name (e.g. zookeeper)",
        show_default=False,
    ),
) -> DefaultAgent:
    with open(f"output/issues/{project_name}.json", "r") as f:
        issues = json.load(f)
    parent_commit = issues[issue_id]["parent_commit"]
    print(f"Checking out {parent_commit}")
    subprocess.run(
        ["git", "-C", f"/mnt/Ubuntu/{project_name}", "checkout", f"{parent_commit}"]
    )
    with open(f"output/prompts/{project_name}/{issue_id}.txt", "r") as f:
        prompt = f.read()
    logging.basicConfig(level=logging.DEBUG)
    config = yaml.safe_load(Path("agent/default.yaml").read_text())

    env_config = config["environment"]
    env_config["cwd"] = f"/mnt/Ubuntu/{project_name}"

    agent_config = config["agent"]
    agent_config["output_path"] = f"output/agent/{issue_id}_log.json"
    if "instance_template" in agent_config:
        agent_config["instance_template"] = agent_config["instance_template"].replace(
            "{{issue_id}}", issue_id
        )

    agent = DefaultAgent(
        LitellmModel(**config["model"]),
        LocalEnvironment(**env_config),
        **agent_config,
    )
    agent.run(prompt)
    return agent


if __name__ == "__main__":
    app()

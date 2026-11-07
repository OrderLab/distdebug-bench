"""Agent harnesses: Mini-SWE-Agent (the paper's main harness), Codex CLI and OpenCode.

A model config picks its harness with a top-level ``harness`` key
(``mini-swe-agent`` when absent); see ``configs/<harness>/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

HARNESSES = ("mini-swe-agent", "codex", "opencode")


def config_harness(path: Path) -> str:
    import yaml

    harness = (yaml.safe_load(Path(path).read_text()) or {}).get("harness", "mini-swe-agent")
    if harness not in HARNESSES:
        raise ValueError(f"{path}: unknown harness {harness!r}; expected one of {HARNESSES}")
    return harness


def load_config(path: Path) -> Any:
    """``AgentConfig`` for Mini-SWE-Agent configs, ``CLIConfig`` for Codex CLI and OpenCode configs."""
    import yaml

    if config_harness(path) == "mini-swe-agent":
        from distdebug_bench.agent import AgentConfig

        return AgentConfig.from_yaml(path)
    from distdebug_bench.harnesses.cli import CLIConfig

    return CLIConfig.from_dict(yaml.safe_load(Path(path).read_text()))


def run_attempt(bug, workspace: Path, run_dir: Path, config: Any) -> dict[str, Any]:
    """Run one attempt with the harness that ``config`` belongs to."""
    from distdebug_bench.harnesses.cli import CLIConfig, run_cli_attempt

    if isinstance(config, CLIConfig):
        return run_cli_attempt(bug, workspace, run_dir, config)
    from distdebug_bench.agent import run_attempt as run_mini_attempt

    return run_mini_attempt(bug, workspace, run_dir, config)

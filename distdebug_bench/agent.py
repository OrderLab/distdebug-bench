"""Run Mini-SWE-Agent on one bug, following the paper's evaluation protocol.

Each attempt runs in an anonymized workspace (see ``repos.temporary_checkout``)
with the issue's attachments copied to ``.artifacts/`` and a schema validator at
``.validate_diagnosis.py``. Commands run inside bubblewrap. The attempt succeeds
when the agent leaves a schema-valid ``diagnosis.json`` in the workspace.

A run directory receives ``trajectory.json``, ``diagnosis.json`` (on success),
and ``session_stats.json`` (API calls, tokens, cost, duration).
"""

from __future__ import annotations

import json
import os
import shutil
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from distdebug_bench import validate_diagnosis
from distdebug_bench.models import BugReport

PROMPT_CONFIG = Path(__file__).with_name("mini_swe_agent_prompt.yaml")
ARTIFACTS_DIR = ".artifacts"
VALIDATOR_NAME = ".validate_diagnosis.py"
DIAGNOSIS_NAME = "diagnosis.json"


@dataclass
class AgentConfig:
    """One model configuration, loaded from ``configs/mini-swe-agent/<model>.yaml``."""

    name: str
    model_name: str
    model_class: str | None = None
    model_kwargs: dict[str, Any] = field(default_factory=dict)
    cost_limit: float = 3.0
    env: dict[str, str] = field(default_factory=dict)
    # Extra mini-swe-agent settings as dotted keys, e.g. {"agent.step_limit": 100}.
    mini_config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path) -> "AgentConfig":
        import yaml

        path = Path(path).resolve()
        data = yaml.safe_load(path.read_text())
        env = {}
        for key, value in (data.get("env") or {}).items():
            value = str(value).lower() if isinstance(value, bool) else str(value)
            # Relative file paths (e.g. a pricing registry) resolve against the config.
            if (path.parent / value).is_file():
                value = str(path.parent / value)
            env[key] = value
        return cls(
            name=data["name"],
            model_name=data["model_name"],
            model_class=data.get("model_class"),
            model_kwargs=data.get("model_kwargs") or {},
            cost_limit=float(data.get("cost_limit", 3.0)),
            env=env,
            mini_config=data.get("mini_config") or {},
        )


def build_task(bug: BugReport, artifact_names: list[str]) -> str:
    """The task text given to the agent: issue title, description, and attachments."""
    task = bug.task_prompt
    if artifact_names:
        listing = "\n".join(f"- {name}" for name in artifact_names)
        task += (
            f"\n\nAdditional artifacts are available in `./{ARTIFACTS_DIR}/`.\n"
            "Inspect the relevant artifact files directly instead of assuming they are "
            "already summarized in the issue report.\n"
            f"Available artifact files:\n{listing}\n"
        )
    return task


def stage_workspace(bug: BugReport, workspace: Path) -> list[str]:
    """Copy the validator and the issue's attachments into the workspace."""
    shutil.copyfile(validate_diagnosis.__file__, workspace / VALIDATOR_NAME)
    if not bug.artifact_dir:
        return []
    staged = workspace / ARTIFACTS_DIR
    staged.mkdir(exist_ok=True)
    names = []
    for path in sorted(bug.artifact_dir.iterdir()):
        if path.is_file():
            shutil.copy2(path, staged / path.name)
            names.append(path.name)
    return names


@contextmanager
def _environment(overrides: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_attempt(bug: BugReport, workspace: Path, run_dir: Path, config: AgentConfig) -> dict[str, Any]:
    """Run one attempt in ``workspace`` and record it in ``run_dir``."""
    from minisweagent.run.mini import main as mini_main

    run_dir.mkdir(parents=True, exist_ok=True)
    trajectory_path = run_dir / "trajectory.json"
    for name in ("trajectory.json", DIAGNOSIS_NAME, "session_stats.json"):
        (run_dir / name).unlink(missing_ok=True)

    task = build_task(bug, stage_workspace(bug, workspace))
    env = {"MSWEA_CONFIGURED": "true", "MSWEA_MODEL_NAME": config.model_name, **config.env}
    start = time.perf_counter()
    error = None
    try:
        with _environment(env):
            mini_main(
                model_name=config.model_name,
                model_class=config.model_class,
                agent_class=None,
                environment_class=None,
                task=task,
                yolo=True,
                cost_limit=config.cost_limit,
                config_spec=[
                    "mini.yaml",
                    str(PROMPT_CONFIG),
                    f"model.model_kwargs={json.dumps(config.model_kwargs)}",
                    f"environment.cwd={json.dumps(str(workspace))}",
                    *(f"{key}={json.dumps(value)}" for key, value in config.mini_config.items()),
                ],
                output=trajectory_path,
                exit_immediately=True,
            )
        diagnosis = json.loads((workspace / DIAGNOSIS_NAME).read_text())
        if errors := validate_diagnosis.validate_diagnosis_payload(diagnosis):
            raise ValueError("Invalid diagnosis.json: " + "; ".join(errors))
        (run_dir / DIAGNOSIS_NAME).write_text(json.dumps(diagnosis, indent=2))
    except Exception as exc:  # noqa: BLE001 - every failure is recorded, then retried
        error = f"{exc}\n{traceback.format_exc()}"

    stats = session_stats(trajectory_path, time.perf_counter() - start, error)
    (run_dir / "session_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    return {"bug": bug.name, "run": run_dir.name, "success": error is None, **stats}


# --------------------------------------------------------------------------- #
# Usage accounting
# --------------------------------------------------------------------------- #


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.replace(",", "").strip().isdigit():
        return int(value.replace(",", "").strip())
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        if (number := _to_int(value)) is not None:
            return number
    return None


def _message_usage(message: dict[str, Any]) -> dict[str, Any]:
    if isinstance(message.get("usage"), dict):
        return message["usage"]
    extra = message.get("extra") or {}
    if isinstance(extra.get("usage"), dict):
        return extra["usage"]
    response = extra.get("response") or {}
    return response.get("usage") if isinstance(response.get("usage"), dict) else {}


def session_stats(trajectory_path: Path, duration: float, error: str | None) -> dict[str, Any]:
    """API calls, token counts, and cost of an attempt, read from its trajectory."""
    totals = dict.fromkeys(
        ("input_tokens", "input_cached_tokens", "output_tokens", "output_reasoning_tokens", "total_tokens"), 0
    )
    steps, cost, api_calls = 0, None, None
    try:
        trajectory = json.loads(trajectory_path.read_text())
    except (OSError, json.JSONDecodeError):
        trajectory = {}
    for msg in trajectory.get("messages", []):
        if not isinstance(msg, dict):
            continue
        usage = _message_usage(msg)
        if msg.get("role") == "assistant" or usage:
            steps += 1
        if not usage:
            continue
        details_in = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        details_out = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        inp = _first_int(usage.get("input_tokens"), usage.get("prompt_tokens"))
        out = _first_int(usage.get("output_tokens"), usage.get("completion_tokens"))
        total = _first_int(usage.get("total_tokens"))
        if total is None and inp is not None and out is not None:
            total = inp + out
        for key, value in (
            ("input_tokens", inp),
            ("input_cached_tokens", _first_int(usage.get("input_cached_tokens"), usage.get("cached_input_tokens"), details_in.get("cached_tokens"))),
            ("output_tokens", out),
            ("output_reasoning_tokens", _first_int(usage.get("output_reasoning_tokens"), usage.get("reasoning_tokens"), details_out.get("reasoning_tokens"))),
            ("total_tokens", total),
        ):
            totals[key] += value or 0
    model_stats = (trajectory.get("info") or {}).get("model_stats") or {}
    if isinstance(model_stats.get("instance_cost"), (int, float)):
        cost = float(model_stats["instance_cost"])
    api_calls = _to_int(model_stats.get("api_calls"))
    return {
        "duration_seconds": duration,
        "api_calls": steps if api_calls is None else api_calls,
        **totals,
        "cost": cost,
        "failed_reason": error,
    }

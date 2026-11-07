"""Run a CLI coding agent (Codex CLI or OpenCode) on one bug.

The attempt uses the same anonymized workspace, task text, attachments and
validator as the Mini-SWE-Agent harness, and the same instructions (rendered from
``mini_swe_agent_prompt.yaml``). The CLI runs inside the sandbox described in
:mod:`distdebug_bench.harnesses.isolation`, with a throwaway home directory, so no
user configuration, MCP servers, hooks, plugins or credentials are loaded.

A run directory receives the CLI's JSON event log (``codex_exec.log`` or
``opencode_cli.log``), its stderr, ``prompt.txt``, ``proxy.log``,
``diagnosis.json`` (on success) and ``session_stats.json``.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from distdebug_bench import validate_diagnosis
from distdebug_bench.agent import DIAGNOSIS_NAME, PROMPT_CONFIG, build_task, stage_workspace
from distdebug_bench.harnesses import isolation
from distdebug_bench.models import BugReport

_MINI_FINISH = "6. Only after validation succeeds, finish by issuing:\n   `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`"
_CLI_FINISH = "6. Only after validation succeeds, stop and reply with a one-sentence summary."


@dataclass
class CLIConfig:
    """One model configuration for a CLI harness, loaded from ``configs/<harness>/<model>.yaml``."""

    name: str
    harness: str  # "codex" or "opencode"
    model: str  # model id at the provider, e.g. "gpt-5.2-codex"
    base_url: str = "https://api.openai.com/v1"  # OpenAI-compatible Responses API endpoint
    api_key_env: str = "OPENAI_API_KEY"  # environment variable holding the API key
    reasoning_effort: str | None = "medium"
    executable: str | None = None  # default: "codex" / "opencode" on PATH
    timeout_seconds: int = 3600
    pricing: dict[str, float] = field(default_factory=dict)  # USD per 1M tokens: input, cached_input, output

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CLIConfig":
        known = set(cls.__dataclass_fields__)
        if unknown := set(data) - known:
            raise ValueError(f"Unknown keys in {data.get('name')} config: {sorted(unknown)}")
        return cls(**data)


def cli_prompt(task: str) -> str:
    """The Mini-SWE-Agent instructions, with the finishing step adapted to a CLI agent."""
    import yaml

    templates = yaml.safe_load(PROMPT_CONFIG.read_text())["agent"]
    body = templates["instance_template"].split("<system_information>")[0]
    if _MINI_FINISH not in body:
        raise RuntimeError("mini_swe_agent_prompt.yaml changed; update the CLI finishing step")
    body = body.replace(_MINI_FINISH, _CLI_FINISH).replace("{{task}}", task)
    return f"{templates['system_template'].strip()}\n\n{body.strip()}\n"


def harness_module(name: str) -> ModuleType:
    from distdebug_bench.harnesses import codex, opencode

    modules = {"codex": codex, "opencode": opencode}
    if name not in modules:
        raise ValueError(f"Unknown CLI harness {name!r}; expected one of {sorted(modules)}")
    return modules[name]


def resolve_executable(config: CLIConfig) -> Path:
    harness = harness_module(config.harness)
    found = shutil.which(os.path.expanduser(config.executable or harness.DEFAULT_EXECUTABLE))
    if not found:
        raise FileNotFoundError(f"{config.executable or harness.DEFAULT_EXECUTABLE} not found; set `executable` in the config")
    return Path(found).absolute()


def check_ready(config: CLIConfig) -> None:
    """Fail fast if the API key, the CLI or bubblewrap is missing."""
    if not os.environ.get(config.api_key_env):
        raise RuntimeError(f"Set {config.api_key_env} to the API key for {config.base_url}")
    isolation.model_api_host(config.base_url)
    resolve_executable(config)
    if shutil.which("bwrap") is None:
        raise RuntimeError("bubblewrap (bwrap) is required to run CLI agents")


def run_cli(config: CLIConfig, workspace: Path, run_dir: Path, prompt: str) -> dict[str, Any]:
    """Run the CLI once on ``prompt`` in ``workspace``; return its exit code, duration and gateway summary."""
    harness = harness_module(config.harness)
    executable = resolve_executable(config)
    api_key = os.environ.get(config.api_key_env)
    if not api_key:
        raise RuntimeError(f"Set {config.api_key_env} to the API key for {config.base_url}")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prompt.txt").write_text(prompt)
    home = Path(tempfile.mkdtemp(prefix=".agent-home-", dir=workspace.parent))
    support = Path(tempfile.mkdtemp(prefix="distdebug-"))  # short path: unix socket names are limited
    try:
        isolation.write_support_files(support)
        gateway = isolation.ModelGateway(
            support / "proxy.sock", config.base_url, api_key, run_dir / "proxy.log", model=config.model
        )
        argv, extra_env = harness.command(config, executable, prompt, workspace, home, gateway.local_base_url)
        # The CLI only gets a placeholder key; the gateway adds the real one outside the sandbox.
        env = isolation.sandbox_env(home, [executable.parent], {config.api_key_env: isolation.PLACEHOLDER_KEY, **extra_env})
        cmd = isolation.bwrap_command(
            argv, workspace=workspace, home=home, support_dir=support,
            read_only=[isolation.install_root(executable)],
        )
        with gateway:
            start = time.perf_counter()
            code = isolation.run_sandboxed(
                cmd, env, run_dir / harness.LOG_NAME, run_dir / harness.STDERR_NAME, config.timeout_seconds
            )
            duration = time.perf_counter() - start
        return {"exit_code": code, "duration_seconds": duration, "network": gateway.summary()}
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(support, ignore_errors=True)


def run_cli_attempt(bug: BugReport, workspace: Path, run_dir: Path, config: CLIConfig) -> dict[str, Any]:
    """Run one attempt of a CLI harness in ``workspace`` and record it in ``run_dir``."""
    harness = harness_module(config.harness)
    run_dir.mkdir(parents=True, exist_ok=True)
    for name in (harness.LOG_NAME, harness.STDERR_NAME, DIAGNOSIS_NAME, "session_stats.json", "proxy.log", "prompt.txt"):
        (run_dir / name).unlink(missing_ok=True)

    prompt = cli_prompt(build_task(bug, stage_workspace(bug, workspace)))
    start = time.perf_counter()
    error, result = None, {}
    try:
        result = run_cli(config, workspace, run_dir, prompt)
        if result["exit_code"] is None:
            raise TimeoutError(f"{config.harness} did not finish within {config.timeout_seconds} s")
        diagnosis_path = workspace / DIAGNOSIS_NAME
        if not diagnosis_path.exists():
            raise FileNotFoundError(f"{config.harness} exited with code {result['exit_code']} without writing {DIAGNOSIS_NAME}")
        diagnosis = json.loads(diagnosis_path.read_text())
        if errors := validate_diagnosis.validate_diagnosis_payload(diagnosis):
            raise ValueError("Invalid diagnosis.json: " + "; ".join(errors))
        (run_dir / DIAGNOSIS_NAME).write_text(json.dumps(diagnosis, indent=2))
    except Exception as exc:  # noqa: BLE001 - every failure is recorded, then retried
        error = f"{exc}\n{traceback.format_exc()}"

    usage = harness.usage(run_dir / harness.LOG_NAME) if (run_dir / harness.LOG_NAME).exists() else {}
    stats = {
        "harness": config.harness,
        "duration_seconds": result.get("duration_seconds", time.perf_counter() - start),
        "api_calls": usage.pop("api_calls", None),
        **{k: usage.get(k, 0) for k in ("input_tokens", "input_cached_tokens", "output_tokens", "output_reasoning_tokens", "total_tokens")},
        "cost": _cost(config, usage),
        "exit_code": result.get("exit_code"),
        "network": result.get("network"),
        "failed_reason": error,
    }
    (run_dir / "session_stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    return {"bug": bug.name, "run": run_dir.name, "success": error is None, **stats}


def _cost(config: CLIConfig, usage: dict[str, Any]) -> float | None:
    if config.pricing:
        cached = usage.get("input_cached_tokens", 0)
        return (
            (usage.get("input_tokens", 0) - cached) * config.pricing.get("input", 0)
            + cached * config.pricing.get("cached_input", config.pricing.get("input", 0))
            + usage.get("output_tokens", 0) * config.pricing.get("output", 0)
        ) / 1e6
    return usage.get("cost") or None

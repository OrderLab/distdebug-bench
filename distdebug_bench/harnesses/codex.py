"""Codex CLI harness (``codex exec``).

Codex runs with an empty ``CODEX_HOME`` (no user config, MCP servers, hooks,
skills or credentials), a custom Responses API provider, web search disabled, and
its own ``workspace-write`` sandbox for the commands it runs, all inside the
network-isolated sandbox from :mod:`distdebug_bench.harnesses.isolation`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LOG_NAME = "codex_exec.log"  # ``codex exec --json`` events
STDERR_NAME = "codex_stderr.log"
DEFAULT_EXECUTABLE = "codex"
PROVIDER = "distdebug"


def command(
    config: Any, executable: Path, prompt: str, workspace: Path, home: Path, base_url: str
) -> tuple[list[str], dict[str, str]]:
    """``codex exec`` argv and environment; ``base_url`` is the sandbox's model API gateway."""
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True, exist_ok=True)
    settings = {
        "model_provider": PROVIDER,
        f"model_providers.{PROVIDER}.name": "DistDebug-Bench",
        f"model_providers.{PROVIDER}.base_url": base_url,
        f"model_providers.{PROVIDER}.env_key": config.api_key_env,
        f"model_providers.{PROVIDER}.wire_api": "responses",
        "web_search": "disabled",
        "check_for_update_on_startup": False,
    }
    if config.reasoning_effort:
        settings["model_reasoning_effort"] = config.reasoning_effort
    argv = [
        str(executable), "exec", "--json", "--skip-git-repo-check",
        "--sandbox", "workspace-write", "--cd", str(workspace), "--model", config.model,
    ]
    for key, value in settings.items():
        argv += ["-c", f"{key}={json.dumps(value)}"]  # JSON scalars and arrays are valid TOML
    return [*argv, prompt], {"CODEX_HOME": str(codex_home)}


def usage(log_path: Path) -> dict[str, Any]:
    """Token counts from ``turn.completed`` events (Codex does not report per-request calls)."""
    totals = dict.fromkeys(("input_tokens", "input_cached_tokens", "output_tokens", "output_reasoning_tokens"), 0)
    for line in log_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        u = event.get("usage") or {}
        totals["input_tokens"] += u.get("input_tokens", 0)
        totals["input_cached_tokens"] += u.get("cached_input_tokens", 0)
        totals["output_tokens"] += u.get("output_tokens", 0)
        totals["output_reasoning_tokens"] += u.get("reasoning_output_tokens", 0)
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return totals

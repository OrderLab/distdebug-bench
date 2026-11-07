"""OpenCode harness (``opencode run``).

OpenCode runs with private XDG config/data/cache directories and its
configuration passed inline: one explicitly declared model on an OpenAI-compatible
provider, the web fetch and web search tools disabled, no MCP servers, no plugins,
and no project, Claude Code or external skill configuration, all inside the
network-isolated sandbox from :mod:`distdebug_bench.harnesses.isolation`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LOG_NAME = "opencode_cli.log"  # ``opencode run --format json`` events
STDERR_NAME = "opencode_stderr.log"
DEFAULT_EXECUTABLE = "opencode"
PROVIDER = "openai"  # OpenCode's bundled OpenAI provider (Responses API), pointed at ``base_url``
WEB_TOOLS = ("webfetch", "websearch", "codesearch")

ENV = {
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
    "OPENCODE_DISABLE_MODELS_FETCH": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
    "OPENCODE_DISABLE_SHARE": "1",
}


def opencode_config(config: Any, base_url: str) -> dict[str, Any]:
    model_options = {"reasoningEffort": config.reasoning_effort} if config.reasoning_effort else {}
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{PROVIDER}/{config.model}",
        "provider": {
            PROVIDER: {
                "options": {"baseURL": base_url, "apiKey": f"{{env:{config.api_key_env}}}"},
                "models": {config.model: {"name": config.model, "options": model_options}},
            }
        },
        "tools": {tool: False for tool in WEB_TOOLS},
        "permission": {"webfetch": "deny", "edit": "allow", "bash": "allow"},
        "mcp": {},
        "plugin": [],
        "share": "disabled",
        "autoupdate": False,
    }


def command(
    config: Any, executable: Path, prompt: str, workspace: Path, home: Path, base_url: str
) -> tuple[list[str], dict[str, str]]:
    """``opencode run`` argv and environment; ``base_url`` is the sandbox's model API gateway."""
    argv = [str(executable), "run", "--format", "json", "--model", f"{PROVIDER}/{config.model}", prompt]
    return argv, {**ENV, "OPENCODE_CONFIG_CONTENT": json.dumps(opencode_config(config, base_url))}


def usage(log_path: Path) -> dict[str, Any]:
    """Token counts, cost and model calls from ``step_finish`` events."""
    totals = dict.fromkeys(("input_tokens", "input_cached_tokens", "output_tokens", "output_reasoning_tokens"), 0)
    calls, cost = 0, 0.0
    for line in log_path.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") if isinstance(event, dict) else None
        if not isinstance(part, dict) or part.get("type") != "step-finish":
            continue
        tokens = part.get("tokens") or {}
        cache = tokens.get("cache") or {}
        calls += 1
        cost += part.get("cost") or 0
        # OpenCode counts cached input separately; report input including cache, as the other harnesses do.
        totals["input_tokens"] += tokens.get("input", 0) + cache.get("read", 0) + cache.get("write", 0)
        totals["input_cached_tokens"] += cache.get("read", 0)
        totals["output_tokens"] += tokens.get("output", 0)
        totals["output_reasoning_tokens"] += tokens.get("reasoning", 0)
    totals["total_tokens"] = totals["input_tokens"] + totals["output_tokens"]
    return {**totals, "api_calls": calls, "cost": cost}

"""Bubblewrap sandbox for agent commands: no network, selected commands disabled.

Same as mini-swe-agent's ``BubblewrapEnvironment``, plus:

- ``allow_network`` (default false): commands run in their own network namespace
  with only loopback, so the agent cannot reach the internet.
- ``blocked_commands``: each listed command is shadowed by a stub that fails with
  exit code 127. The paper blocks ``chafa`` (crashes runs) and ``img2txt`` (hangs
  them). Commands can also be listed in ``MSWEA_BLOCKED_COMMANDS`` (comma-separated).
"""

from __future__ import annotations

import os

from minisweagent.environments.extra.bubblewrap import BubblewrapEnvironment, BubblewrapEnvironmentConfig

STUB_MOUNT = "/.mswea-blocked-bin"
DEFAULT_PATH = "/usr/local/bin:/usr/sbin:/usr/bin:/bin"


class SandboxEnvironmentConfig(BubblewrapEnvironmentConfig):
    allow_network: bool = False
    """Whether commands may use the host's network."""
    blocked_commands: list[str] = []
    """Commands to shadow with fast-failing stubs inside the sandbox."""


class SandboxEnvironment(BubblewrapEnvironment):
    def __init__(self, **kwargs):
        super().__init__(config_class=SandboxEnvironmentConfig, **kwargs)
        if not self.config.allow_network:
            self.config.wrapper_args = [*self.config.wrapper_args, "--unshare-net"]
        commands = self._blocked_commands()
        if not commands:
            return
        stub_dir = self.working_dir / "blocked-bin"
        stub_dir.mkdir()
        for command in commands:
            stub = stub_dir / command
            stub.write_text(f"#!/bin/sh\necho '{command} is disabled in this evaluation sandbox.' >&2\nexit 127\n")
            stub.chmod(0o755)
        # The upstream execute() appends wrapper_args, then env, to the bwrap call.
        self.config.wrapper_args = [*self.config.wrapper_args, "--ro-bind", str(stub_dir), STUB_MOUNT]
        self.config.env = {**self.config.env, "PATH": f"{STUB_MOUNT}:{self.config.env.get('PATH', DEFAULT_PATH)}"}

    def _blocked_commands(self) -> list[str]:
        names = [*self.config.blocked_commands, *os.getenv("MSWEA_BLOCKED_COMMANDS", "").split(",")]
        valid = []
        for name in (n.strip() for n in names):
            if not name or name in valid:
                continue
            if "/" in name or not all(ch.isalnum() or ch in "._-" for ch in name):
                self.logger.warning("Ignoring invalid blocked command name: %r", name)
                continue
            valid.append(name)
        return valid

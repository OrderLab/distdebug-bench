"""Bubblewrap sandbox for agent commands: no network, selected commands disabled.

Same as mini-swe-agent's ``BubblewrapEnvironment``, plus:

- ``allow_network`` (default false): commands run in their own network namespace
  with only loopback, so the agent cannot reach the internet. On startup the
  sandbox tries an outbound connection and refuses to run if it succeeds.
- ``blocked_commands``: each listed command is shadowed by a stub that fails with
  exit code 127. The paper blocks ``chafa`` (crashes runs) and ``img2txt`` (hangs
  them). Commands can also be listed in ``MSWEA_BLOCKED_COMMANDS`` (comma-separated).
"""

from __future__ import annotations

import os

from minisweagent.environments.extra.bubblewrap import BubblewrapEnvironment, BubblewrapEnvironmentConfig

STUB_MOUNT = "/.mswea-blocked-bin"
DEFAULT_PATH = "/usr/local/bin:/usr/sbin:/usr/bin:/bin"
# Prints "reachable <target>" if any well-known public address accepts a TCP connection.
NETWORK_PROBE = r"""
for target in 1.1.1.1/443 8.8.8.8/53; do
  if timeout 3 bash -c "exec 3<>/dev/tcp/$target" 2>/dev/null; then echo "reachable $target"; exit 0; fi
done
echo unreachable
"""


class NetworkIsolationError(RuntimeError):
    """The sandbox can reach the network although ``allow_network`` is false."""


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
        self._install_command_stubs()
        if not self.config.allow_network:
            self.check_network_isolation()

    def check_network_isolation(self) -> None:
        """Raise ``NetworkIsolationError`` if a command in the sandbox can open an outbound connection."""
        result = self.execute({"command": NETWORK_PROBE}, cwd=str(self.working_dir), timeout=30)
        if "reachable" not in result["output"]:
            raise NetworkIsolationError(f"Network self-check did not complete: {result['output'][-500:]!r}")
        if not result["output"].strip().endswith("unreachable"):
            raise NetworkIsolationError(
                f"Sandboxed commands can reach the network ({result['output'].strip()}); refusing to run. "
                "Check that bwrap supports --unshare-net on this host."
            )

    def _install_command_stubs(self) -> None:
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

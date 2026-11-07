"""Network isolation: the Mini-SWE-Agent sandbox self-check and the CLI harness sandbox."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from distdebug_bench.harnesses import isolation


def _bwrap_works() -> bool:
    return shutil.which("bwrap") is not None and subprocess.run(
        ["bwrap", "--unshare-all", "--ro-bind", "/", "/", "true"], capture_output=True
    ).returncode == 0


needs_bwrap = pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap with network namespaces is unavailable")


# --- Mini-SWE-Agent sandbox ---------------------------------------------------------------------


@needs_bwrap
def test_sandbox_self_check_passes_without_network():
    pytest.importorskip("minisweagent")
    from distdebug_bench.sandbox import NETWORK_PROBE, SandboxEnvironment

    env = SandboxEnvironment()  # runs the self-check
    assert env.execute({"command": NETWORK_PROBE})["output"].strip() == "unreachable"


@needs_bwrap
def test_sandbox_self_check_refuses_a_reachable_network(monkeypatch):
    pytest.importorskip("minisweagent")
    from distdebug_bench import sandbox

    monkeypatch.setattr(sandbox, "NETWORK_PROBE", "echo reachable 1.1.1.1/443")
    with pytest.raises(sandbox.NetworkIsolationError, match="refusing to run"):
        sandbox.SandboxEnvironment()


# --- CLI harness sandbox ------------------------------------------------------------------------


class _FakeModelAPI(BaseHTTPRequestHandler):
    """Stands in for the model API: echoes what it received, streaming the reply in chunks."""

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        reply = json.dumps({"path": self.path, "authorization": self.headers.get("Authorization"),
                            "body": json.loads(body or b"{}")}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for i in range(0, len(reply), 16):
            piece = reply[i : i + 16]
            self.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeModelAPI)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/openai/v1"
    server.shutdown()


SCRIPT = r"""
set -u
echo "proxied-github=$(curl -s -m 10 -o /dev/null -w '%{http_code}' https://github.com; echo " exit=$?")"
echo "direct-github=$(curl -s -m 10 --noproxy '*' -o /dev/null https://github.com; echo "exit=$?")"
echo "direct-ip=$(curl -s -m 10 --noproxy '*' -o /dev/null https://1.1.1.1; echo "exit=$?")"
echo "plain-http=$(curl -s -m 10 -o /dev/null -w '%{http_code}' http://example.com/; echo " exit=$?")"
echo "model-call=$(curl -s -m 10 -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -d '{"model": "m", "input": "hi"}' "$BASE_URL/responses")"
echo "web-search=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d '{"model": "m", "tools": [{"type": "web_search"}]}' "$BASE_URL/responses")"
echo "other-model=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
  -d '{"model": "bigger-model", "input": "hi"}' "$BASE_URL/responses")"
echo "other-path=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$BASE_URL/files")"
echo "home=$(ls -A "$HOME" | wc -l) users=$(ls /home 2>/dev/null | wc -l)"
"""


@needs_bwrap
@pytest.mark.skipif(shutil.which("curl") is None, reason="curl is unavailable")
def test_cli_sandbox_reaches_only_the_model_api(tmp_path, fake_api):
    workspace, home, support = tmp_path / "workspace", tmp_path / "home", tmp_path / "s"
    for d in (workspace, home, support):
        d.mkdir()
    isolation.write_support_files(support)
    gateway = isolation.ModelGateway(support / "proxy.sock", fake_api, "real-key", tmp_path / "proxy.log", model="m")
    env = isolation.sandbox_env(home, [], {"API_KEY": isolation.PLACEHOLDER_KEY, "BASE_URL": gateway.local_base_url})
    cmd = isolation.bwrap_command(["bash", "-c", SCRIPT], workspace=workspace, home=home, support_dir=support)
    with gateway:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=120)
    out = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)

    assert out["proxied-github"] == "000 exit=56", result.stdout  # proxy answered 403 to CONNECT
    assert out["direct-github"] == "exit=6"  # no DNS in the sandbox
    assert out["direct-ip"] == "exit=7"  # no route out
    assert out["plain-http"].startswith("403")
    echoed = json.loads(out["model-call"])
    assert echoed["path"] == "/openai/v1/responses" and echoed["body"] == {"model": "m", "input": "hi"}
    assert echoed["authorization"] == "Bearer real-key"  # added outside; the sandbox only had the placeholder
    assert out["web-search"] == "403" and out["other-model"] == "403" and out["other-path"] == "403"
    assert out["home"].startswith("0 ") and out["home"].endswith("users=0")

    log = [json.loads(line) for line in (tmp_path / "proxy.log").read_text().splitlines()]
    assert {e["target"] for e in log if e["decision"] == "denied"} >= {"github.com:443", "example.com:80"}
    assert [e["path"] for e in log if e["decision"] == "forwarded"] == ["/openai/v1/responses"]
    assert gateway.summary()["denied"]["github.com:443"] == 1


def test_hosted_tools_are_detected():
    assert isolation._hosted_tools(b'{"tools": [{"type": "function"}, {"type": "web_search_preview"}]}') == [
        "web_search_preview"
    ]
    assert isolation._hosted_tools(b'{"tools": [{"type": "function"}, {"type": "custom"}]}') == []


def test_cli_prompt_matches_the_mini_swe_agent_instructions():
    pytest.importorskip("yaml")
    from distdebug_bench.harnesses.cli import cli_prompt

    prompt = cli_prompt("Title: Leader loses data")
    assert "Title: Leader loses data" in prompt and ".validate_diagnosis.py" in prompt
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in prompt and "{{" not in prompt


@pytest.mark.parametrize("harness", ["codex", "opencode"])
def test_cli_commands_disable_web_tools(tmp_path, harness):
    from distdebug_bench.harnesses.cli import CLIConfig, harness_module

    config = CLIConfig(name="x", harness=harness, model="gpt-5.2-codex")
    argv, env = harness_module(harness).command(config, Path("/opt/cli"), "PROMPT", tmp_path, tmp_path, "http://gw/v1")
    assert argv[-1] == "PROMPT"
    if harness == "codex":
        assert 'web_search="disabled"' in argv and "workspace-write" in argv
        assert 'model_providers.distdebug.base_url="http://gw/v1"' in argv
        assert env["CODEX_HOME"].startswith(str(tmp_path))
    else:
        settings = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert settings["tools"] == {"webfetch": False, "websearch": False, "codesearch": False}
        assert settings["provider"]["openai"]["options"]["baseURL"] == "http://gw/v1"
        assert "gpt-5.2-codex" in settings["provider"]["openai"]["models"]
        assert settings["mcp"] == {} and settings["plugin"] == []
        assert env["OPENCODE_DISABLE_DEFAULT_PLUGINS"] == "1"

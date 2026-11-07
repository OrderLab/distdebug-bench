"""Sandbox for CLI agents: no network except the model API, and no API key.

The agent CLI (Codex CLI, OpenCode) runs inside bubblewrap in its own network
namespace, which has only loopback, so neither the CLI nor any command it runs can
open a connection to the outside. The single way out is a model API gateway that
runs on the host and listens on a unix socket bind-mounted into the sandbox; a
small forwarder inside the sandbox exposes it as ``http://127.0.0.1:<PROXY_PORT>``.

- The CLI is pointed at the gateway as its OpenAI-compatible endpoint and only
  holds a placeholder key. The gateway adds the real key and forwards Responses /
  Chat Completions / model-list requests to the configured endpoint, so the key
  never enters the sandbox.
- The gateway only serves the configured model and refuses requests that enable
  hosted tools (web search, file search, code interpreter, remote MCP, ...), so
  the model API cannot be used to browse or to consult another model.
- ``HTTPS_PROXY``/``HTTP_PROXY`` also point at the gateway, which answers every
  proxy request for another host with ``403 Forbidden``; connections that bypass
  the proxy fail because the namespace has no route out.
- Only the workspace, a throwaway home directory, the CLI's install directory and
  system directories (``/usr``, ``/etc``, ...) are visible; the user's home is not.

Every gateway decision is logged to ``proxy.log`` in the run directory.
"""

from __future__ import annotations

import datetime
import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
from collections import Counter
from pathlib import Path
from typing import BinaryIO, Iterable
from urllib.parse import urlsplit

SANDBOX_DIR = "/.distdebug"  # support files inside the sandbox
PROXY_PORT = 18080
PROXY_URL = f"http://127.0.0.1:{PROXY_PORT}"
SYSTEM_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/etc")

# Runs inside the sandbox with the system python3: 127.0.0.1:<port> -> unix socket.
FORWARDER = r'''
import socket, sys, threading

port, target = int(sys.argv[1]), sys.argv[2]


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def serve(client):
    upstream = socket.socket(socket.AF_UNIX)
    try:
        upstream.connect(target)
    except OSError:
        client.close()
        return
    other = threading.Thread(target=pipe, args=(client, upstream), daemon=True)
    other.start()
    pipe(upstream, client)
    other.join()
    client.close()
    upstream.close()


server = socket.create_server(("127.0.0.1", port))
while True:
    conn, _ = server.accept()
    threading.Thread(target=serve, args=(conn,), daemon=True).start()
'''

# Starts the forwarder, waits until it listens, then runs the agent CLI ("$@").
LAUNCHER = f"""
python3 {SANDBOX_DIR}/forward.py {PROXY_PORT} {SANDBOX_DIR}/proxy.sock 2>/dev/null &
for _ in $(seq 100); do
  (exec 3<>/dev/tcp/127.0.0.1/{PROXY_PORT}) 2>/dev/null && break
  sleep 0.05
done
exec "$@"
"""


PLACEHOLDER_KEY = "sk-distdebug-sandbox"  # what the CLI sees instead of the real key
# Model API paths (relative to the endpoint's base path) that the gateway forwards.
MODEL_PATHS = ("responses", "chat/completions", "models")
# Hosted tools run by the provider rather than in the sandbox; any of these could reach the web.
HOSTED_TOOL_PREFIXES = ("web_search", "file_search", "code_interpreter", "computer", "mcp", "image_generation")
_HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
               "te", "trailer", "transfer-encoding", "upgrade"}


def model_api_host(base_url: str) -> str:
    """``host:port`` of an API base URL."""
    parts = urlsplit(base_url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError(f"The model API must be an http(s):// URL, got {base_url!r}")
    return f"{parts.hostname.lower()}:{parts.port or (443 if parts.scheme == 'https' else 80)}"


def _read_headers(rfile: BinaryIO) -> list[tuple[str, str]] | None:
    headers = []
    while True:
        line = rfile.readline(65537)
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            return headers
        name, _, value = line.decode("latin-1").partition(":")
        headers.append((name.strip(), value.strip()))


def _read_body(rfile: BinaryIO, headers: list[tuple[str, str]]) -> bytes:
    fields = {k.lower(): v for k, v in headers}
    if "chunked" in fields.get("transfer-encoding", "").lower():
        body = b""
        while True:
            size = int(rfile.readline(1024).split(b";")[0].strip() or b"0", 16)
            if size == 0:
                _read_headers(rfile)  # trailers
                return body
            body += rfile.read(size)
            rfile.readline(3)
    return rfile.read(int(fields.get("content-length", "0") or 0))


class ModelGateway:
    """HTTP gateway (on a unix socket) to one model on one API endpoint; refuses everything else."""

    def __init__(
        self, socket_path: Path, base_url: str, api_key: str, log_path: Path | None = None, model: str | None = None
    ):
        parts = urlsplit(base_url)
        self.socket_path = Path(socket_path)
        self.upstream = model_api_host(base_url)
        self.scheme = parts.scheme
        self.prefix = parts.path.rstrip("/")
        self.api_key = api_key
        self.model = model  # None: any model
        self.log_path = log_path
        self.forwarded: Counter[str] = Counter()
        self.denied: Counter[str] = Counter()
        self._lock = threading.Lock()
        self._server: socket.socket | None = None

    @property
    def local_base_url(self) -> str:
        """Base URL the sandboxed CLI uses for the model API."""
        return f"{PROXY_URL}{self.prefix}"

    def __enter__(self) -> "ModelGateway":
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(str(self.socket_path))
        self._server.listen(64)
        threading.Thread(target=self._accept_loop, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.close()
        self.socket_path.unlink(missing_ok=True)

    def summary(self) -> dict:
        return {"model_api": self.upstream, "forwarded": dict(self.forwarded), "denied": dict(self.denied)}

    def _log(self, decision: str, target: str, **detail) -> None:
        with self._lock:
            (self.forwarded if decision == "forwarded" else self.denied)[target] += 1
            if self.log_path is not None:
                entry = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                         "decision": decision, "target": target, **detail}
                with open(self.log_path, "a") as log:
                    log.write(json.dumps(entry) + "\n")

    def _accept_loop(self) -> None:
        assert self._server is not None
        while True:
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        rfile, wfile = client.makefile("rb"), client.makefile("wb")
        try:
            while self._handle(rfile, wfile):
                pass
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            for f in (rfile, wfile):
                try:
                    f.close()
                except OSError:
                    pass
            client.close()

    def _refuse(self, wfile: BinaryIO, status: str, message: str) -> None:
        body = json.dumps({"error": {"message": message, "type": "sandbox_policy"}}).encode()
        wfile.write(f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        wfile.flush()

    def _handle(self, rfile: BinaryIO, wfile: BinaryIO) -> bool:
        """Serve one request; return whether the connection stays open."""
        request_line = rfile.readline(65537).decode("latin-1").strip()
        if not request_line:
            return False
        method, target = (request_line.split() + ["", ""])[:2]
        headers = _read_headers(rfile)
        if headers is None:
            return False
        if method.upper() == "CONNECT":
            host, _, port = target.rpartition(":")
            self._log("denied", f"{host.strip('[]').lower()}:{port}", reason="only the model API is reachable")
            self._refuse(wfile, "403 Forbidden", "Network access is disabled in this sandbox.")
            return False
        if "://" in target:  # absolute-form proxy request
            parts = urlsplit(target)
            if f"{parts.hostname}:{parts.port or 80}" not in {f"127.0.0.1:{PROXY_PORT}", f"localhost:{PROXY_PORT}"}:
                self._log("denied", f"{parts.hostname}:{parts.port or 80}", reason="only the model API is reachable")
                self._refuse(wfile, "403 Forbidden", "Network access is disabled in this sandbox.")
                return False
            target = parts.path + (f"?{parts.query}" if parts.query else "")
        body = _read_body(rfile, headers)
        relative = target[len(self.prefix):].lstrip("/") if target.startswith(self.prefix + "/") else None
        if relative is None or not relative.startswith(MODEL_PATHS):
            self._log("denied", self.upstream, method=method, path=target, reason="not a model API path")
            self._refuse(wfile, "403 Forbidden", "Only the model API is reachable from this sandbox.")
            return False
        requested = _requested_model(body)
        if self.model is not None and requested is not None and requested != self.model:
            self._log("denied", self.upstream, method=method, path=target, model=requested, reason="not the configured model")
            self._refuse(wfile, "403 Forbidden", f"Only {self.model} is available in this sandbox.")
            return False
        if hosted := _hosted_tools(body):
            self._log("denied", self.upstream, method=method, path=target, reason=f"hosted tools: {hosted}")
            self._refuse(wfile, "403 Forbidden", f"Hosted tools are disabled in this sandbox: {hosted}")
            return False
        self._forward(method, target, headers, body, wfile, requested)
        return not any(k.lower() == "connection" and v.lower() == "close" for k, v in headers)

    def _forward(
        self, method: str, path: str, headers: list[tuple[str, str]], body: bytes, wfile: BinaryIO, model: str | None
    ) -> None:
        host, _, port = self.upstream.rpartition(":")
        connection_class = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        upstream = connection_class(host, int(port), timeout=900)
        try:
            skip = _HOP_BY_HOP | {"host", "authorization", "api-key", "content-length"}
            out = {k: v for k, v in headers if k.lower() not in skip}
            out["Authorization"] = f"Bearer {self.api_key}"
            upstream.request(method, path, body=body if body or method in {"POST", "PUT", "PATCH"} else None, headers=out)
            response = upstream.getresponse()
            self._log("forwarded", self.upstream, method=method, path=path, model=model, status=response.status)
            length = response.getheader("Content-Length")
            lines = [f"HTTP/1.1 {response.status} {response.reason}"]
            lines += [f"{k}: {v}" for k, v in response.getheaders() if k.lower() not in _HOP_BY_HOP | {"content-length"}]
            lines.append(f"Content-Length: {length}" if length is not None else "Transfer-Encoding: chunked")
            wfile.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
            while chunk := response.read1(65536):
                wfile.write(chunk if length is not None else b"%x\r\n%s\r\n" % (len(chunk), chunk))
                wfile.flush()
            if length is None:
                wfile.write(b"0\r\n\r\n")
            wfile.flush()
        finally:
            upstream.close()


def _requested_model(body: bytes) -> str | None:
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload.get("model") if isinstance(payload, dict) else None


def _hosted_tools(body: bytes) -> list[str]:
    try:
        payload = json.loads(body) if body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    tools = payload.get("tools") if isinstance(payload, dict) else None
    types = [str(t.get("type", "")) for t in tools or [] if isinstance(t, dict)]
    return [t for t in types if t.startswith(HOSTED_TOOL_PREFIXES)]


def install_root(executable: str | Path) -> Path:
    """Directory to expose read-only so a CLI can run: its ``node_modules`` tree or its own directory."""
    resolved = Path(executable).resolve()
    for parent in resolved.parents:
        if parent.name == "node_modules":
            return parent
    return resolved.parent


def write_support_files(support_dir: Path) -> None:
    (support_dir / "forward.py").write_text(FORWARDER)


def bwrap_command(
    argv: list[str],
    *,
    workspace: Path,
    home: Path,
    support_dir: Path,
    read_only: Iterable[Path] = (),
) -> list[str]:
    """bubblewrap invocation that runs ``argv`` in ``workspace`` without network access.

    ``support_dir`` (holding ``proxy.sock`` and ``forward.py``) appears at ``SANDBOX_DIR``.
    """
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise RuntimeError("bubblewrap (bwrap) is required to run CLI agents")
    cmd = [bwrap, "--unshare-all", "--die-with-parent", "--new-session"]
    for path in SYSTEM_DIRS:
        if os.path.islink(path):  # merged /usr: /bin -> usr/bin
            cmd += ["--symlink", os.readlink(path), path]
        else:
            cmd += ["--ro-bind-try", path, path]
    cmd += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    for path in dict.fromkeys(Path(p) for p in read_only):
        cmd += ["--ro-bind", str(path), str(path)]
    cmd += ["--bind", str(home), str(home)]
    cmd += ["--ro-bind", str(support_dir), SANDBOX_DIR]
    cmd += ["--bind", str(workspace), str(workspace), "--chdir", str(workspace)]
    return [*cmd, "bash", "-c", LAUNCHER, "bash", *argv]


def sandbox_env(home: Path, path_dirs: Iterable[Path], extra: dict[str, str]) -> dict[str, str]:
    """Environment for the sandboxed CLI: proxy settings, a private home, and ``extra``."""
    path = ":".join([*(str(p) for p in dict.fromkeys(path_dirs)), "/usr/local/bin", "/usr/bin", "/bin"])
    env = {
        "PATH": path,
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local/share"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "NO_PROXY": "127.0.0.1,localhost",  # the CLI talks to the gateway directly
        "no_proxy": "127.0.0.1,localhost",
    }
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env[name] = PROXY_URL
    return {**env, **extra}


def run_sandboxed(cmd: list[str], env: dict[str, str], stdout: Path, stderr: Path, timeout: float) -> int | None:
    """Run ``cmd``; return its exit code, or None if it timed out (the sandbox is killed)."""
    with open(stdout, "w") as out, open(stderr, "w") as err:
        process = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            return None

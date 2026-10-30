#!/usr/bin/env python3
"""MCP protocol client (Phase 3 module 1). Standard library only.

Speaks MCP JSON-RPC 2.0 to a server from a registry entry (see
registrylib.py: mcp_servers entries carry id/command/args/env/
enabled). Two transports: stdio (newline-delimited JSON to a
launched server process) and — Phase 5, for agent-registered
remote servers — Streamable HTTP (POST JSON-RPC, Mcp-Session-Id
header, JSON or SSE-framed responses).

Security rules (read before extending):
- Server commands come ONLY from enabled registry entries. An entry
  that is disabled or absent is refused, never executed.
- Env values in a registry entry may be literal or "secret:<name>";
  the latter is resolved via agentlib.read_secret (agent-owned 0600
  files). Resolved values are NEVER logged, NEVER put in events,
  NEVER echoed in errors.
- Tool arguments pass through verbatim to the server; the agent
  approval runtime (agentlib) gates the mcp_call step BEFORE this
  module runs, so this module never re-decides approval.
- Timeouts are enforced on every request; a dead server fails the
  call loudly instead of hanging the daemon.

Protocol (MCP 2024-11-05, stdio transport):
  initialize -> {protocolVersion, capabilities, serverInfo}
  notifications/initialized (no response)
  tools/list -> {tools: [{name, description, inputSchema}]}
  tools/call {name, arguments} -> {content, isError}

Reuse notes (evaluation cards §9):
- The JSON-RPC shapes follow the MCP spec as implemented by OpenClaw's
  agent-bundle MCP harness (tools/list + tools/call over a managed
  server process); the client itself is DclawOS-written in stdlib
  Python because the service has no Node runtime dependency here.
// reuse-check: openclaw MCP harness NOT copied, reason: the harness
  is TypeScript bound to the gateway's bundle runtime; the DclawOS
  service is stdlib Python, so only the protocol shapes transfer.
"""

import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/usr/local/lib/dclawos")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "lib", "dclawos"))

import agentlib  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
REQUEST_TIMEOUT_DEFAULT = 60


class MCPError(Exception):
    pass


def _resolve_env(env):
    """Resolve an env mapping; secret:<name> values via agentlib.

    Literal values pass through. A missing secret fails loudly.
    """
    out = {}
    for key, value in (env or {}).items():
        if isinstance(value, str) and value.startswith("secret:"):
            out[key] = agentlib.read_secret(value[len("secret:"):])
        else:
            out[key] = value
    return out


def load_server_entry(server_id):
    """Return the enabled registry entry for a server id, or raise."""
    import registrylib
    data, _skipped = registrylib.load_registry()
    for entry in data.get("mcp_servers", []):
        if entry.get("id") == server_id:
            if not entry.get("enabled", True):
                raise MCPError(f"MCP server disabled: {server_id!r}")
            if not entry.get("command") and not entry.get("url"):
                raise MCPError(
                    f"MCP server {server_id!r} has no command or url")
            return entry
    raise MCPError(f"unknown MCP server: {server_id!r}")


class MCPClient:
    def __init__(self, entry, timeout=REQUEST_TIMEOUT_DEFAULT):
        self.entry = entry
        self.timeout = timeout
        self.proc = None
        self.next_id = 1

    def start(self):
        env = dict(os.environ)
        env.update(_resolve_env(self.entry.get("env")))
        args = [self.entry["command"]] + list(self.entry.get("args") or [])
        try:
            self.proc = subprocess.Popen(
                args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env)
        except (FileNotFoundError, OSError) as exc:
            raise MCPError(f"cannot launch MCP server: {exc}")
        reply = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "dclawos-agent", "version": "3.0"},
        })
        if "error" in reply:
            raise MCPError(f"initialize failed: {reply['error']}")
        self.notify("notifications/initialized", {})
        return reply.get("result", {})

    def request(self, method, params):
        if self.proc is None or self.proc.poll() is not None:
            raise MCPError("MCP server process is not running")
        request_id = self.next_id
        self.next_id += 1
        line = json.dumps({"jsonrpc": "2.0", "id": request_id,
                           "method": method, "params": params}) + "\n"
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"write to MCP server failed: {exc}")
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise MCPError("MCP server exited mid-request")
            import select
            ready, _, _ = select.select([self.proc.stdout], [], [], 1.0)
            if not ready:
                continue
            raw = self.proc.stdout.readline()
            if not raw:
                raise MCPError("MCP server closed stdout")
            try:
                reply = json.loads(raw)
            except ValueError:
                continue
            if reply.get("id") == request_id:
                return reply
        raise MCPError(f"MCP request timed out: {method}")

    def notify(self, method, params):
        if self.proc is None or self.proc.poll() is not None:
            raise MCPError("MCP server process is not running")
        line = json.dumps({"jsonrpc": "2.0", "method": method,
                           "params": params}) + "\n"
        try:
            self.proc.stdin.write(line)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise MCPError(f"notify MCP server failed: {exc}")

    def list_tools(self):
        reply = self.request("tools/list", {})
        if "error" in reply:
            raise MCPError(f"tools/list failed: {reply['error']}")
        return (reply.get("result") or {}).get("tools", [])

    def call(self, name, arguments):
        reply = self.request("tools/call",
                             {"name": name,
                              "arguments": arguments or {}})
        if "error" in reply:
            raise MCPError(f"tools/call failed: {reply['error']}")
        result = reply.get("result") or {}
        if result.get("isError"):
            content = result.get("content") or []
            text = " ".join(str(c.get("text", "")) for c in content)
            raise MCPError(f"tool reported error: {text[:500]}")
        return result

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self.proc.kill()
                except OSError:
                    pass
        self.proc = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


class MCPHttpClient:
    """Streamable-HTTP transport with the MCPClient interface.

    One session per client lifetime: initialize captures the
    Mcp-Session-Id response header; every later POST carries it.
    Responses may be plain JSON or SSE-framed (data: lines); the
    last JSON-RPC object in the body wins.
    """

    def __init__(self, entry, timeout=REQUEST_TIMEOUT_DEFAULT):
        self.entry = entry
        self.timeout = timeout
        self.next_id = 1
        self.session_id = None

    def _post(self, payload, expect_reply=True):
        import urllib.request
        url = self.entry.get("url")
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "User-Agent": "DclawOS-agent/2.0"}
        headers.update(_resolve_env(self.entry.get("env")))
        # NOTE: env for HTTP servers carries HEADERS (e.g. an
        # Authorization bearer via secret:<name>), unlike stdio
        # where env is the process environment.
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(url, data=data,
                                         headers=headers,
                                         method="POST")
        try:
            with urllib.request.urlopen(
                    request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", "replace")
                session = response.headers.get("Mcp-Session-Id")
        except Exception as exc:  # noqa: BLE001 - faults are data
            raise MCPError(f"MCP HTTP request failed: {exc}")
        if session:
            self.session_id = session
        if not expect_reply or not raw.strip():
            return {}
        return _parse_rpc_body(raw)

    def start(self):
        reply = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "dclawos-agent", "version": "3.0"},
        })
        if "error" in reply:
            raise MCPError(f"initialize failed: {reply['error']}")
        self.notify("notifications/initialized", {})
        return reply.get("result", {})

    def request(self, method, params):
        request_id = self.next_id
        self.next_id += 1
        return self._post({"jsonrpc": "2.0", "id": request_id,
                           "method": method, "params": params})

    def notify(self, method, params):
        self._post({"jsonrpc": "2.0", "method": method,
                    "params": params}, expect_reply=False)

    def list_tools(self):
        reply = self.request("tools/list", {})
        if "error" in reply:
            raise MCPError(f"tools/list failed: {reply['error']}")
        return (reply.get("result") or {}).get("tools", [])

    def call(self, name, arguments):
        reply = self.request("tools/call",
                             {"name": name,
                              "arguments": arguments or {}})
        if "error" in reply:
            raise MCPError(f"tools/call failed: {reply['error']}")
        result = reply.get("result") or {}
        if result.get("isError"):
            content = result.get("content") or []
            text = " ".join(str(c.get("text", "")) for c in content)
            raise MCPError(f"tool reported error: {text[:500]}")
        return result

    def stop(self):
        self.session_id = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def _parse_rpc_body(raw):
    """Parse a JSON or SSE-framed JSON-RPC response body."""
    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    found = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            parsed = json.loads(line[len("data:"):].strip())
        except ValueError:
            continue
        if isinstance(parsed, dict):
            found = parsed
    if found:
        return found
    raise MCPError("MCP HTTP response is not valid JSON-RPC")


def _client_for(entry, timeout):
    if entry.get("transport") == "http" or entry.get("url"):
        return MCPHttpClient(entry, timeout=timeout)
    return MCPClient(entry, timeout=timeout)


def validate_server(entry, timeout=REQUEST_TIMEOUT_DEFAULT):
    """Handshake check used by mcp_add BEFORE registration:
    initialize + tools/list must succeed. Returns tool names.
    Raises MCPError on any failure — the caller registers nothing."""
    with _client_for(entry, timeout) as client:
        tools = client.list_tools()
    return [str(t.get("name", "")) for t in tools
            if isinstance(t, dict)]


def call_tool(params, _ticket_id):
    """Daemon entry point: params {server, tool, arguments}.

    One call = one server session lifetime (launch/connect, call,
    stop). Long-lived sessions are a later optimization;
    session-per-call keeps supervision trivial and failures
    isolated.
    """
    server_id = params.get("server")
    tool = params.get("tool")
    if not isinstance(server_id, str) or not server_id:
        raise ValueError("mcp_call needs a server id")
    if not isinstance(tool, str) or not tool:
        raise ValueError("mcp_call needs a tool name")
    entry = load_server_entry(server_id)
    timeout = params.get("timeout") or REQUEST_TIMEOUT_DEFAULT
    with _client_for(entry, timeout) as client:
        result = client.call(tool, params.get("arguments") or {})
    return {"server": server_id, "tool": tool, "result": result}

#!/usr/bin/env python3
"""Connector runner for the DclawOS agent service (Phase 3 module 2).

A connector is a registry entry (registrylib "connectors" category) whose
command is a channel adapter: it reads one JSON object on stdin and
writes one JSON object on stdout. Actions: "send" (v1) and "poll" (Phase 4 module 8, inbound):
the adapter takes a human-readable message and delivers it to its
channel, returning {"ok": true, ...} or {"ok": false, "error": ...}.

Contract notes:
- Only enabled registry entries may run (allow-list, same rule as MCP
  servers and skills). Disabled or unknown connectors are refused.
- The message is passed on stdin as JSON, never on argv, so it cannot
  leak into a process list.
- secret:<name> values in the entry env are resolved from the agent
  secret store (agentlib.read_secret); resolved values are passed to
  the child environment but never logged and never echoed back in
  results.
- Timeout default 60 s, output capped at 8 KiB. A connector that
  returns invalid JSON is an error, not a success.

// reuse-check: OpenClaw connectors are channel adapters with a
  send/receive contract (openclaw/src/... gateway connectors); the
  transport shape (stdin JSON / stdout JSON) and the allow-list rule
  follow that convention, but the code here is DclawOS-written stdlib
  Python — no OpenClaw connector code is copied (the upstream
  adapters are TypeScript + channel SDKs, not portable to a
  stdlib-only Python service).
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agentlib  # noqa: E402
import registrylib  # noqa: E402

OUTPUT_CAP = 8192


def resolve_connector(connector_id, registry_path=None):
    """Return the enabled registry entry for connector_id.

    Raises ValueError for unknown, disabled, or malformed entries.
    """
    data, _skipped = registrylib.load_registry(registry_path)
    for entry in data.get("connectors", []):
        if entry.get("id") == connector_id:
            if not entry.get("enabled", True):
                raise ValueError(f"connector disabled: {connector_id!r}")
            return entry
    raise ValueError(f"unknown connector: {connector_id!r}")


def list_connectors(registry_path=None):
    """Return enabled connectors as [{id, name, description}]."""
    data, _skipped = registrylib.load_registry(registry_path)
    return [{"id": e["id"], "name": e["name"],
             "description": e.get("description", "")}
            for e in data.get("connectors", [])
            if e.get("enabled", True)]


def _adapter_env(entry):
    env = dict(os.environ)
    for key, value in entry.get("env", {}).items():
        if isinstance(value, str) and value.startswith("secret:"):
            env[key] = agentlib.read_secret(value[len("secret:"):])
        else:
            env[key] = value
    return env


def _run_adapter(entry, payload, timeout=60):
    """Run one adapter request/response round trip. Returns the
    adapter's JSON reply with secret-shaped keys stripped."""
    try:
        proc = subprocess.run(
            [entry["command"]] + list(entry.get("args", [])),
            input=json.dumps(payload).encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=timeout, env=_adapter_env(entry))
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "connector timed out"}
    except OSError as exc:
        return {"ok": False, "error": f"connector failed to start: {exc}"}
    out = proc.stdout.decode("utf-8", "replace")[:OUTPUT_CAP]
    if proc.returncode != 0:
        return {"ok": False,
                "error": f"connector exited {proc.returncode}: "
                         f"{proc.stderr.decode('utf-8', 'replace')[:200]}"}
    try:
        reply = json.loads(out)
    except ValueError:
        return {"ok": False, "error": "connector returned invalid JSON"}
    if not isinstance(reply, dict):
        return {"ok": False, "error": "connector reply is not an object"}
    return {k: v for k, v in reply.items()
            if k != "secret" and not k.endswith("_secret")}


def send_message(connector_id, message, timeout=60, registry_path=None):
    """Send message via connector_id. Returns the adapter's JSON reply.

    The reply is returned verbatim except that any key named "secret"
    or ending in "_secret" is stripped before it leaves this function,
    so a chatty adapter cannot leak credentials into task results.
    """
    entry = resolve_connector(connector_id, registry_path)
    return _run_adapter(entry, {"action": "send", "message": message},
                        timeout=timeout)


def poll_events(connector_id, timeout=60, registry_path=None):
    """Poll a connector for inbound events (Phase 4 module 8, C4).

    Adapter contract extension: {"action": "poll"} ->
    {"ok": true, "events": [{"id": ..., "message": "...",
    "sender": ...}, ...]}. The adapter owns its delivery cursor
    (each event is returned once). Adapters that do not implement
    poll answer with an "unknown action" error, reported here as
    {"ok": False, "unsupported": True} so the daemon can skip them
    quietly. Events are normalised to {id, text, sender}.
    """
    entry = resolve_connector(connector_id, registry_path)
    reply = _run_adapter(entry, {"action": "poll"}, timeout=timeout)
    if not reply.get("ok"):
        error = str(reply.get("error", ""))
        if "unknown action" in error:
            return {"ok": False, "unsupported": True, "events": []}
        return {"ok": False, "error": error, "events": []}
    events = []
    for raw in reply.get("events", []):
        if not isinstance(raw, dict):
            continue
        text = raw.get("message") or raw.get("text") or ""
        if not isinstance(text, str) or not text:
            continue
        events.append({
            "id": str(raw.get("id", "")),
            "text": text,
            "sender": str(raw.get("sender", "")),
        })
    return {"ok": True, "events": events}

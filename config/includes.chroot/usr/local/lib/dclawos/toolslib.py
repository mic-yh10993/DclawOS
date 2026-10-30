#!/usr/bin/env python3
"""Model-facing tool schemas (Phase 4 module 2, gap row A5).

OpenClaw constructs the model's tool list at runtime
(src/agents/agent-tools.ts:184,254,677; core-coding-tools.ts:204).
DclawOS's dispatch set already exists (agentlib.TOOLS +
dclawos-agent.execute_step); what was missing is the schema layer
that serialises those tools into an LLM `tools[]` parameter. This
module is that layer: one schema per dispatch tool, kept beside the
classify() tiers so a new tool must appear in both places.

// reuse-check: OpenClaw tool definitions NOT copied, reason: their
  schemas are written against OpenClaw's gateway tool objects
  (TypeBox, host/node routing); the portable contract is the
  OpenAI tools[] shape (name + description + JSON-Schema
  parameters), which these schemas implement for the DclawOS tool
  set with upstream's descriptions as the behavioural reference.

Schema parameter names are exactly the step params execute_step
consumes, so a model tool_call maps 1:1 onto a task step:
{name, arguments(JSON string)} -> {tool: name, params: arguments}.
"""

import json

_STR = {"type": "string"}
_OBJ = {"type": "object"}


def _schema(name, description, properties, required=()):
    params = {"type": "object", "properties": properties}
    if required:
        params["required"] = list(required)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params,
        },
    }


TOOL_SCHEMAS = [
    _schema("shell",
            "Run a shell command (argv array, no shell interpolation) "
            "in the agent workspace. Mutating: needs approval.",
            {"argv": {"type": "array", "items": _STR},
             "cwd": _STR, "timeout": {"type": "number"}},
            ("argv",)),
    _schema("read_file", "Read a text file (max 1MB).",
            {"path": _STR}, ("path",)),
    _schema("write_file", "Write a text file, creating parent dirs.",
            {"path": _STR, "content": _STR}, ("path", "content")),
    _schema("edit_file",
            "Replace the single exact occurrence of old_text with "
            "new_text in a file. Fails if old_text is absent or not "
            "unique.",
            {"path": _STR, "old_text": _STR, "new_text": _STR},
            ("path", "old_text", "new_text")),
    _schema("list_files", "List the entries of a directory.",
            {"path": _STR}),
    _schema("process",
            "Manage background shell processes: action start (argv), "
            "poll, log or kill (process_id).",
            {"action": {"type": "string",
                        "enum": ["start", "poll", "log", "kill"]},
             "argv": {"type": "array", "items": _STR},
             "process_id": _STR},
            ("action",)),
    _schema("web_fetch",
            "Fetch a web page over HTTP(S) and return its text "
            "(size capped, tags stripped). Read-only.",
            {"url": _STR}, ("url",)),
    _schema("service_status", "Report the status of a systemd service.",
            {"name": _STR}, ("name",)),
    _schema("service_restart",
            "Restart a systemd service via the privileged broker. "
            "Needs approval.",
            {"name": _STR}, ("name",)),
    _schema("package_install",
            "Install a Debian package via the privileged broker. "
            "Needs approval.",
            {"name": _STR}, ("name",)),
    _schema("route_decide",
            "Ask the routing engine which provider/model should "
            "serve a task. Read-only.",
            {}),
    _schema("mcp_call",
            "Call a tool on a configured MCP server.",
            {"server": _STR, "tool": _STR, "arguments": _OBJ},
            ("server", "tool")),
    _schema("skill_run", "Run an installed skill by id.",
            {"skill": _STR, "input": _STR}, ("skill",)),
    _schema("subagent",
            "Delegate a goal to a child agent session with its own "
            "conversation; returns the child's final reply.",
            {"goal": _STR}, ("goal",)),
    _schema("takeover_request",
            "Ask the human to take over a GUI step (captcha, login). "
            "Returns only an outcome and a session handle, never "
            "credentials.",
            {"kind": _STR, "message": _STR, "app_hint": _STR},
            ("message",)),
    _schema("memory_save",
            "Save a fact or preference to long-term memory so "
            "future sessions can recall it.",
            {"text": _STR, "tag": _STR}, ("text",)),
    _schema("memory_search",
            "Search long-term memory entries by substring.",
            {"query": _STR}, ("query",)),
    _schema("cron",
            "Manage scheduled agent jobs: action add (schedule + "
            "goal), list or remove (job_id). Schedule is "
            "'every <seconds>' or a 5-field cron expression.",
            {"action": {"type": "string",
                        "enum": ["add", "list", "remove"]},
             "schedule": _STR, "goal": _STR, "job_id": _STR},
            ("action",)),
    _schema("connector_send",
            "Send a message through a configured connector.",
            {"connector": _STR, "message": _STR},
            ("connector", "message")),
    _schema("registry_list",
            "List registered skills, MCP servers, connectors and "
            "channels (env values redacted). Use this to discover "
            "ids before calling them.",
            {"kind": {"type": "string",
                      "enum": ["skills", "mcp_servers",
                               "connectors", "channels"]}}),
    _schema("skill_install",
            "Install a skill yourself from a local directory, a "
            "local .tar.gz/.zip archive, an archive URL, or a git "
            "URL. The package must contain SKILL.md and a runnable "
            "command (or scripts/run.sh / skill.py / index.js). "
            "A license string is REQUIRED and recorded with the "
            "source as provenance. Validated before registering; "
            "failures install nothing. Needs tier-2 approval.",
            {"id": _STR, "source": _STR, "license": _STR,
             "name": _STR, "description": _STR, "command": _STR,
             "args": {"type": "array", "items": _STR}},
            ("id", "source", "license")),
    _schema("skill_remove",
            "Remove a skill: drops its registry entry and, for "
            "agent-installed skills, deletes the installed payload.",
            {"id": _STR}, ("id",)),
    _schema("mcp_add",
            "Register an MCP server yourself: a stdio command or "
            "an http(s) URL. The server must answer a live MCP "
            "handshake (initialize + tools/list) before the entry "
            "is kept; a failed validation registers nothing. env "
            "values for secret-looking keys must be secret:<name> "
            "references. A license string is REQUIRED. Needs "
            "tier-2 approval.",
            {"id": _STR, "license": _STR, "command": _STR,
             "args": {"type": "array", "items": _STR},
             "url": _STR, "env": _OBJ, "name": _STR,
             "description": _STR},
            ("id", "license")),
    _schema("mcp_remove", "Remove a registered MCP server.",
            {"id": _STR}, ("id",)),
    _schema("channel_manage",
            "Manage messaging channels: action list, add (kind + "
            "optional allowlist; a human must still save the bot "
            "token in Settings), remove, enable, disable, "
            "set_allowlist. Tokens can never be set through this "
            "tool.",
            {"action": {"type": "string",
                        "enum": ["list", "add", "remove", "enable",
                                 "disable", "set_allowlist"]},
             "channel": _STR, "kind": _STR, "name": _STR,
             "options": _OBJ,
             "allowlist": {"type": "array", "items": _STR}},
            ("action",)),
]

SCHEMA_NAMES = [s["function"]["name"] for s in TOOL_SCHEMAS]


def tools_for_model():
    """The tools[] payload for an LLM request (deep copy)."""
    return json.loads(json.dumps(TOOL_SCHEMAS))


def parse_tool_call(call):
    """Map one model tool_call onto a task step dict.

    Returns {tool, params} or raises ValueError on bad input: the
    loop treats a malformed tool call as data for the model, never
    as an instruction to execute something unvalidated.
    """
    if not isinstance(call, dict):
        raise ValueError("tool call is not an object")
    fn = call.get("function") or {}
    name = fn.get("name")
    if name not in SCHEMA_NAMES:
        raise ValueError(f"unknown tool requested by model: {name!r}")
    raw = fn.get("arguments") or "{}"
    if isinstance(raw, str):
        try:
            params = json.loads(raw)
        except ValueError:
            raise ValueError(f"tool {name}: arguments not valid JSON")
    elif isinstance(raw, dict):
        params = raw
    else:
        raise ValueError(f"tool {name}: arguments must be an object")
    if not isinstance(params, dict):
        raise ValueError(f"tool {name}: arguments must be an object")
    return {"tool": name, "params": params}

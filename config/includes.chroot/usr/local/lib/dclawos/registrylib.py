#!/usr/bin/env python3
"""Unified skills + MCP server registry (Phase 2 module 9, stdlib only).

User-confirmed Q3: skills and MCP are UNIFIED — one registry file, one
library, one GUI page in the settings app, no TUI.

File: /var/lib/dclawos/agent-registry.json (overridable via
DCLAWOS_REGISTRY for tests):
  {"skills": [{id, name, path, description, license, enabled}],
   "mcp_servers": [{id, name, command, args, env, description,
                     license, enabled}]}
IDs match ^[a-z0-9][a-z0-9_-]*$ and are unique within their list.

Scope notes (read before extending):
- This is the MANAGEMENT layer (list / add / remove / enable), not
  the MCP protocol client: full MCP client protocol + tool-call
  approval live in the agent service (later module) and reuse the
  module 5 ticket broker for approval semantics (cards §9).
- The GUI does NOT edit MCP env vars in module 9: env values are
  often secrets, and the secure home for them is libsecret (a later
  addition). The schema already carries env (preserved round-trip)
  so hand-written registries and the future agent service can use it;
  the GUI warns that env is not editable here rather than storing
  secrets in plaintext through a form.
- License policy (cards §9 "third-party skill package licenses checked
  per item"): every entry carries a license field, shown in the GUI.
  Adding an entry WITHOUT a license pops a confirmation dialog — the
  per-item check. No third-party packages are bundled in module 9, so
  there is nothing to check yet; the mechanism is ready.
- load_registry() returns (data, skipped): malformed entries (not a
  dict, or missing a required field) are skipped and counted, never
  silently kept; a corrupt whole file raises ValueError (the GUI
  shows it instead of an empty list pretending all is well).
"""

import grp
import json
import os
import re

REGISTRY_DEFAULT = "/var/lib/dclawos/agent-registry.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SHARED_GROUP_DEFAULT = "dclawos-session"


def registry_path():
    return os.environ.get("DCLAWOS_REGISTRY", REGISTRY_DEFAULT)


def _set_shared_group(path):
    try:
        os.chown(path, -1,
                 grp.getgrnam(os.environ.get(
                     "DCLAWOS_SHARED_GROUP", SHARED_GROUP_DEFAULT)).gr_gid)
    except (KeyError, OSError):
        pass


def _atomic_write(path, text):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    _set_shared_group(tmp)
    try:
        os.chmod(tmp, 0o660)
    except OSError:
        pass
    os.replace(tmp, path)


def _validate_id(entry_id):
    if not isinstance(entry_id, str) or not ID_RE.match(entry_id):
        raise ValueError(
            f"invalid id: {entry_id!r} "
            "(lowercase letters, digits, _ and -, must start alnum)")


def _normalize(entry, required, defaults):
    if not isinstance(entry, dict):
        raise ValueError("entry is not an object")
    normalized = dict(entry)
    for field in required:
        value = normalized.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"entry missing required field: {field}")
    _validate_id(normalized["id"])
    for field, default in defaults.items():
        if field not in normalized:
            normalized[field] = default
    if "enabled" in normalized and not isinstance(
            normalized["enabled"], bool):
        raise ValueError("entry 'enabled' must be true or false")
    return normalized


def normalize_skill(entry):
    normalized = _normalize(
        entry, ("id", "name", "path"),
        {"description": "", "license": "", "enabled": True})
    return normalized


def normalize_server(entry):
    # Phase 5: a server is either stdio (command required) or
    # streamable HTTP (url required, transport == "http"). The
    # original shape (id/name/command) is unchanged for stdio.
    normalized = _normalize(
        entry, ("id", "name"),
        {"command": "", "args": [], "env": {}, "description": "",
         "license": "", "enabled": True, "transport": "stdio",
         "url": ""})
    if normalized["transport"] == "http":
        url = normalized["url"]
        if not isinstance(url, str) or not url.startswith(
                ("http://", "https://")):
            raise ValueError(
                "http mcp server needs an http(s) 'url'")
    elif not isinstance(normalized["command"], str) or \
            not normalized["command"]:
        raise ValueError("entry missing required field: command")
    if not isinstance(normalized["args"], list) or not all(
            isinstance(a, str) for a in normalized["args"]):
        raise ValueError("server 'args' must be a list of strings")
    if not isinstance(normalized["env"], dict):
        raise ValueError("server 'env' must be an object")
    return normalized


# Phase 3 module 2: connectors are a third registry category, with the
# same entry shape as MCP servers (id/name/command required, optional
# args/env/description/license/enabled). A connector is a command-based
# channel adapter: the agent service sends it JSON on stdin
# ({action: "send", message}) and reads JSON on stdout. Unknown fields
# round-trip like the other categories.
def normalize_connector(entry):
    normalized = _normalize(
        entry, ("id", "name", "command"),
        {"args": [], "env": {}, "description": "", "license": "",
         "enabled": True})
    if not isinstance(normalized["args"], list) or not all(
            isinstance(a, str) for a in normalized["args"]):
        raise ValueError("connector 'args' must be a list of strings")
    if not isinstance(normalized["env"], dict):
        raise ValueError("connector 'env' must be an object")
    return normalized


# Phase 5 (WS1): channels are a fourth registry category. A channel
# is a third-party messaging service (Telegram, Discord, ...) the
# agent daemon polls through channellib's built-in adapters —
# unlike connectors, a channel is NOT an arbitrary command: "kind"
# selects a built-in adapter module, "secret" names the agent
# secret file holding the bot token (written only by the human via
# the broker write-secret op), and "allowlist" holds the owner
# account ids allowed to talk to the agent (sender ids as strings;
# an empty allowlist lets NOBODY in). Unknown fields round-trip.
def normalize_channel(entry):
    normalized = _normalize(
        entry, ("id", "name", "kind"),
        {"description": "", "license": "", "enabled": True,
         "allowlist": [], "secret": "", "options": {}})
    allowlist = normalized["allowlist"]
    if not isinstance(allowlist, list) or not all(
            isinstance(a, (str, int)) for a in allowlist):
        raise ValueError("channel 'allowlist' must be a list of ids")
    normalized["allowlist"] = [str(a) for a in allowlist]
    if not isinstance(normalized["secret"], str):
        raise ValueError("channel 'secret' must be a string")
    if not isinstance(normalized["options"], dict):
        raise ValueError("channel 'options' must be an object")
    return normalized


def load_registry(path=None):
    if path is None:
        path = registry_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"skills": [], "mcp_servers": [], "connectors": [],
                "channels": []}, 0
    except (OSError, ValueError) as exc:
        raise ValueError(f"registry unreadable: {exc}")
    if not isinstance(data, dict):
        raise ValueError("registry root is not an object")
    result = {"skills": [], "mcp_servers": [], "connectors": [],
              "channels": []}
    skipped = 0
    for kind, normalizer in (("skills", normalize_skill),
                             ("mcp_servers", normalize_server),
                             ("connectors", normalize_connector),
                             ("channels", normalize_channel)):
        entries = data.get(kind, [])
        if not isinstance(entries, list):
            raise ValueError(f"registry field {kind!r} is not a list")
        for entry in entries:
            try:
                result[kind].append(normalizer(entry))
            except ValueError:
                skipped += 1
    return result, skipped


def save_registry(data, path=None):
    if path is None:
        path = registry_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _atomic_write(
        path,
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n")


def _find(data, kind, entry_id):
    for entry in data[kind]:
        if entry.get("id") == entry_id:
            return entry
    return None


def add_skill(data, entry):
    normalized = normalize_skill(entry)
    if _find(data, "skills", normalized["id"]) is not None:
        raise ValueError(f"skill id already exists: {normalized['id']!r}")
    data["skills"].append(normalized)
    return normalized


def add_server(data, entry):
    normalized = normalize_server(entry)
    if _find(data, "mcp_servers", normalized["id"]) is not None:
        raise ValueError(
            f"mcp server id already exists: {normalized['id']!r}")
    data["mcp_servers"].append(normalized)
    return normalized


def add_connector(data, entry):
    normalized = normalize_connector(entry)
    if _find(data, "connectors", normalized["id"]) is not None:
        raise ValueError(
            f"connector id already exists: {normalized['id']!r}")
    data["connectors"].append(normalized)
    return normalized


def add_channel(data, entry):
    normalized = normalize_channel(entry)
    if _find(data, "channels", normalized["id"]) is not None:
        raise ValueError(
            f"channel id already exists: {normalized['id']!r}")
    data["channels"].append(normalized)
    return normalized


def remove_entry(data, kind, entry_id):
    entry = _find(data, kind, entry_id)
    if entry is None:
        raise ValueError(f"no such {kind[:-1]}: {entry_id!r}")
    data[kind].remove(entry)


def set_enabled(data, kind, entry_id, enabled):
    entry = _find(data, kind, entry_id)
    if entry is None:
        raise ValueError(f"no such {kind[:-1]}: {entry_id!r}")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    entry["enabled"] = enabled
    return entry

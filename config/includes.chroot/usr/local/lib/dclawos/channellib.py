#!/usr/bin/env python3
"""Third-party messaging channel runtime (Phase 5, WS1).

A channel is a registry entry (registrylib "channels" category):
{id, kind, name, enabled, allowlist[], secret, options{}}. Unlike
connectors (arbitrary commands), a channel's "kind" selects a
BUILT-IN adapter module in this package (channel_<kind>), so no
unreviewed code path is introduced by a registry edit.

Semantics (transferred from OpenClaw's channel core — see
docs/PHASE5.md for the inventory anchors):
- Inbound normalisation: every adapter returns events shaped
  {id, text, sender, chat} — the platform's message reduced to
  who said what, where a reply goes.
- Allowlist-first policy: an event whose sender is not in the
  channel's allowlist is dropped HERE (the adapter already
  consumed it, so it is never redelivered) and counted in the
  channel state (state["dropped"]); an empty allowlist drops
  everything. Non-allowlisted senders get no reply and no task.
- The adapter owns its delivery cursor inside its state file
  (one JSON file per channel under the channels state dir), the
  same "adapter owns the cursor" rule as connectors.
- Tokens: entry["secret"] names an agent secret file (0600,
  written by the human through the broker). The value is resolved
  here, handed to the adapter in memory, and never logged, never
  put in events, never returned.

// reuse-check: OpenClaw channel adapters NOT copied, reason: the
  upstream adapters are TypeScript on per-service SDKs inside a
  gateway process; the DclawOS runtime is stdlib Python inside
  the agent daemon. The transferable parts are the SEMANTICS
  (normalised event shape, allowFrom-style allowlist policy,
  reply-to-origin), re-implemented here per docs/PHASE5.md.
"""

import importlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agentlib  # noqa: E402
import registrylib  # noqa: E402

STATE_DIR_DEFAULT = "/var/lib/dclawos/channels"


class ChannelError(Exception):
    pass


def state_dir():
    return os.environ.get("DCLAWOS_CHANNELS_STATE", STATE_DIR_DEFAULT)


def list_channels(registry_path=None):
    """Return enabled channel entries (full registry dicts)."""
    data, _skipped = registrylib.load_registry(registry_path)
    return [e for e in data.get("channels", [])
            if e.get("enabled", True)]


def resolve_channel(channel_id, registry_path=None):
    """Return the enabled registry entry or raise ChannelError."""
    data, _skipped = registrylib.load_registry(registry_path)
    for entry in data.get("channels", []):
        if entry.get("id") == channel_id:
            if not entry.get("enabled", True):
                raise ChannelError(f"channel disabled: {channel_id!r}")
            return entry
    raise ChannelError(f"unknown channel: {channel_id!r}")


def _adapter_module(kind):
    if not isinstance(kind, str) or not kind.replace("_", "").isalnum():
        raise ChannelError(f"invalid channel kind: {kind!r}")
    try:
        return importlib.import_module(f"channel_{kind}")
    except ImportError:
        raise ChannelError(f"no adapter for channel kind: {kind!r}")


def _state_path(channel_id):
    safe = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in channel_id)
    return os.path.join(state_dir(), safe + ".json")


def _load_state(channel_id):
    try:
        with open(_state_path(channel_id), "r",
                  encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save_state(channel_id, state):
    path = _state_path(channel_id)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    agentlib.atomic_write(
        path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _context(entry):
    """Build the adapter context for one channel entry.

    The token is resolved here (and only here): adapters receive
    it as ctx["token"] and must never persist or echo it.
    """
    token = ""
    secret_name = entry.get("secret") or ""
    if secret_name:
        try:
            token = agentlib.read_secret(secret_name)
        except (OSError, ValueError):
            raise ChannelError(
                f"channel {entry.get('id')!r}: secret "
                f"{secret_name!r} unreadable")
    state = _load_state(entry["id"])

    def save_state():
        _save_state(entry["id"], state)

    return {"entry": entry, "token": token, "state": state,
            "save_state": save_state}


def poll_channel(entry):
    """Poll one channel entry. Returns {"ok": True, "events": [...]}
    with ONLY allowlisted events, or {"ok": False, "error": ...}.

    The allowlist filter runs here, not in adapters, so a buggy or
    future adapter cannot widen access by forgetting the check.
    """
    try:
        module = _adapter_module(entry.get("kind"))
        ctx = _context(entry)
        raw_events = module.poll(ctx)
    except ChannelError as exc:
        return {"ok": False, "error": str(exc), "events": []}
    except Exception as exc:  # noqa: BLE001 - adapter faults are data
        return {"ok": False,
                "error": f"adapter {entry.get('kind')} failed: {exc}",
                "events": []}
    allowlist = {str(a) for a in entry.get("allowlist", [])}
    events = []
    dropped = 0
    for raw in raw_events or []:
        if not isinstance(raw, dict):
            continue
        text = raw.get("text")
        if not isinstance(text, str) or not text:
            continue
        sender = str(raw.get("sender", ""))
        if sender not in allowlist:
            dropped += 1
            continue
        events.append({
            "id": str(raw.get("id", "")),
            "text": text,
            "sender": sender,
            "chat": str(raw.get("chat", "")),
        })
    if dropped:
        try:
            ctx = _context(entry)
            state = ctx["state"]
            state["dropped"] = int(state.get("dropped", 0)) + dropped
            ctx["save_state"]()
        except ChannelError:
            pass
    return {"ok": True, "events": events}


def send_message(channel_id, chat_id, text, registry_path=None):
    """Send text to chat_id via channel_id. Returns the adapter's
    reply dict ({"ok": bool, ...}); raises ChannelError only for
    unknown/disabled channels."""
    entry = resolve_channel(channel_id, registry_path)
    module = _adapter_module(entry.get("kind"))
    ctx = _context(entry)
    try:
        return module.send(ctx, chat_id, text)
    except ChannelError:
        raise
    except Exception as exc:  # noqa: BLE001 - adapter faults are data
        return {"ok": False, "error": f"adapter send failed: {exc}"}

#!/usr/bin/env python3
"""Conversation transcript store (Phase 4 module 2, gap row A6).

Layout follows OpenClaw's session store shape
(src/config/sessions/paths.ts: an index file plus one append-only
transcript per session), adapted to DclawOS file conventions:

  /var/lib/dclawos/conversations/sessions.json   index
  /var/lib/dclawos/conversations/<session>.jsonl transcript

// reuse-check: OpenClaw session store NOT copied, reason: upstream's
  store is a combined SQLite/Postgres + gateway service (906 files);
  the portable contract is the on-disk shape (index + JSONL
  transcript with message/compaction entry kinds), which this module
  implements with the same atomic-write discipline as agentlib.

Entries are JSON lines: {ts, kind, ...}. kind "message" carries an
OpenAI-shaped message {role, content, tool_calls?, tool_call_id?,
name?}; kind "compaction" carries {summary, covers} and replaces the
covered history when projecting context (see project_messages).
"""

import json
import os
import re
import time

import agentlib

CONV_DIR_DEFAULT = "/var/lib/dclawos/conversations"
SESSION_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def conv_dir():
    return os.environ.get("DCLAWOS_CONV_DIR", CONV_DIR_DEFAULT)


def _index_path():
    return os.path.join(conv_dir(), "sessions.json")


def transcript_path(session_id):
    if not isinstance(session_id, str) or not SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session id: {session_id!r}")
    return os.path.join(conv_dir(), session_id + ".jsonl")


def _load_index():
    try:
        with open(_index_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return {}


def _save_index(index):
    agentlib.ensure_exchange_dir(conv_dir())
    agentlib.atomic_write(
        _index_path(), json.dumps(index, indent=2, sort_keys=True) + "\n")


def ensure_session(session_id, title=""):
    """Create the index entry if missing. Returns the entry dict."""
    index = _load_index()
    entry = index.get(session_id)
    if entry is None:
        now = int(time.time())
        entry = {
            "id": session_id,
            "title": title or "会话",
            "created_at": now,
            "updated_at": now,
        }
        index[session_id] = entry
        _save_index(index)
        agentlib.ensure_exchange_dir(conv_dir())
        path = transcript_path(session_id)
        if not os.path.exists(path):
            agentlib.atomic_write(path, "")
    return entry


def touch_session(session_id, title=None):
    index = _load_index()
    entry = index.get(session_id)
    if entry is None:
        return ensure_session(session_id, title or "")
    entry["updated_at"] = int(time.time())
    if title:
        entry["title"] = title
    index[session_id] = entry
    _save_index(index)
    return entry


def list_sessions():
    index = _load_index()
    return sorted(index.values(), key=lambda e: e.get("updated_at", 0))


def append_entry(session_id, entry):
    ensure_session(session_id)
    entry = dict(entry)
    entry.setdefault("ts", int(time.time()))
    path = transcript_path(session_id)
    agentlib.ensure_exchange_dir(conv_dir())
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    agentlib.set_shared_group(path)
    touch_session(session_id)


def append_message(session_id, message):
    append_entry(session_id, {"kind": "message", "message": message})


def load_entries(session_id):
    try:
        with open(transcript_path(session_id), "r",
                  encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def project_messages(session_id, system_prompt=None):
    """Project the transcript into the OpenAI-shaped message list the
    model consumes: the latest compaction summary (as a system note)
    replaces everything it covers; later messages follow verbatim."""
    entries = load_entries(session_id)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    start = 0
    for index, entry in enumerate(entries):
        if entry.get("kind") == "compaction":
            start = index
    if start > 0 or (entries and entries[0].get("kind") == "compaction"):
        entry = entries[start]
        messages.append({
            "role": "system",
            "content": "Earlier conversation summary: " +
                       str(entry.get("summary", "")),
        })
        start += 1
    for entry in entries[start:]:
        if entry.get("kind") == "message" and isinstance(
                entry.get("message"), dict):
            messages.append(entry["message"])
    return messages


def message_count(session_id):
    return sum(1 for e in load_entries(session_id)
               if e.get("kind") == "message")

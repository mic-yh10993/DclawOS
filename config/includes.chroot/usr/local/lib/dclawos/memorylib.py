#!/usr/bin/env python3
"""Long-term memory store (Phase 4 module 7, gap row C3).

OpenClaw's memory-core extension keeps agent memory in markdown
files the agent reads and appends to (with optional vector
backends behind plugins — excluded in PHASE4-GAP section D).
This is that file-backed contract, sized for one machine:

  <memory dir>/MEMORY.md — one entry per block:

      ## 2026-10-30T12:00:00Z [tag]
      text...

memory_save appends an entry; memory_search does a
case-insensitive substring scan and returns matching entries
(newest first, capped). DCLAWOS_MEMORY_DIR overrides the
directory for tests.

// reuse-check: OpenClaw memory-core NOT copied, reason: its core
  is a plugin host around hosted vector/embedding services; the
  file-backed markdown store is the portable contract and is what
  upstream itself falls back to. Implemented over plain files.
"""

import os
import time

import agentlib

MEMORY_PATH_DEFAULT = "/var/lib/dclawos/memory/MEMORY.md"
MAX_ENTRY_CHARS = 8000
SEARCH_RESULT_CAP = 10


def memory_path():
    override = os.environ.get("DCLAWOS_MEMORY_PATH")
    if override:
        return override
    directory = os.environ.get("DCLAWOS_MEMORY_DIR")
    if directory:
        return os.path.join(directory, "MEMORY.md")
    return MEMORY_PATH_DEFAULT


def _read_entries():
    try:
        with open(memory_path(), "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return []
    entries = []
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                entries.append(current)
            current = {"header": line[3:].strip(), "body": []}
        elif current is not None:
            current["body"].append(line)
    if current is not None:
        entries.append(current)
    for entry in entries:
        entry["text"] = "\n".join(entry["body"]).strip()
    return entries


def save(text, tag="note"):
    text = (text or "").strip()
    if not text:
        raise ValueError("memory text must not be empty")
    if len(text) > MAX_ENTRY_CHARS:
        text = text[:MAX_ENTRY_CHARS]
    path = memory_path()
    parent = os.path.dirname(path)
    if parent:
        agentlib.ensure_exchange_dir(parent)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    block = f"\n## {stamp} [{tag}]\n{text}\n"
    existing = ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read()
    except OSError:
        existing = "# DclawOS agent memory\n"
    agentlib.atomic_write(path, existing + block)
    return {"saved": True, "at": stamp}


def search(query):
    query = (query or "").strip().lower()
    if not query:
        raise ValueError("memory query must not be empty")
    hits = []
    for entry in reversed(_read_entries()):
        haystack = (entry["header"] + "\n" + entry["text"]).lower()
        if query in haystack:
            hits.append({
                "header": entry["header"],
                "text": entry["text"][:2000],
            })
        if len(hits) >= SEARCH_RESULT_CAP:
            break
    return {"query": query, "matches": hits, "count": len(hits)}

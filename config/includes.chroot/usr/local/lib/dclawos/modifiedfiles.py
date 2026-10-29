#!/usr/bin/env python3
"""Agent-modified-files reader (Phase 2 module 8, stdlib only).

Contract (the future agent service writes this file when it modifies
a file; the editor sidebar reads it):
  /var/lib/dclawos/modified-files.jsonl — one JSON object per line:
  {path, ts, session}
load_modified_files() returns the newest entry per path, newest first,
skipping corrupt lines. It never raises on a bad file: a missing or
corrupt store is an empty list, not a crash.
"""

import json
import os

MODIFIED_FILES_DEFAULT = "/var/lib/dclawos/modified-files.jsonl"


def modified_files_path():
    return os.environ.get("DCLAWOS_MODIFIED_FILES",
                           MODIFIED_FILES_DEFAULT)


def load_modified_files(path=None):
    if path is None:
        path = modified_files_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    newest = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        path_value = entry.get("path")
        if not isinstance(path_value, str) or not path_value:
            continue
        try:
            ts = int(entry.get("ts", 0))
        except (TypeError, ValueError):
            ts = 0
        current = newest.get(path_value)
        if current is None or ts >= int(current.get("ts", 0)):
            newest[path_value] = {
                "path": path_value,
                "ts": ts,
                "session": entry.get("session", ""),
            }
    entries = sorted(newest.values(), key=lambda e: -e["ts"])
    return entries

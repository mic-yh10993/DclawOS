#!/usr/bin/env python3
"""Shared human-takeover request store (Phase 2 module 6).

Flow (user-confirmed Q2): the agent writes a takeover request when it
needs a human (captcha, account login, other verification) -> the human
sees it on the dashboard banner -> the human completes the step in
THEIR OWN session -> the human marks the request in the popup ->
the agent reads back ONLY the outcome + an optional session handle.

Request file: /var/lib/dclawos/takeover/<16-hex-id>.json:
  {id, kind, message, app_hint, status, created_at, completed_at,
   result: {outcome, session_handle, ...extra}}
Status: pending | done | failed | cancelled. The result NEVER contains
credentials: the popup UI has no password field, its session-handle
entry is a plain visible text field labeled "do not enter passwords or
tokens", and write_result drops any extra field whose key looks like a
credential (FORBIDDEN_RESULT_KEYS, case-insensitive). The session
handle is an opaque label the human types (e.g. which account/session
they completed) — it is not a secret and is never logged by the popup.
Kinds: captcha | login | verification | other (unknown kinds display
raw; the popup maps the known ones to Chinese labels).
"""

import grp
import json
import os
import re
import secrets
import time

TAKEOVER_DIR_DEFAULT = "/var/lib/dclawos/takeover"
REQUEST_ID_RE = re.compile(r"^[0-9a-f]{16}$")
OUTCOMES = ("success", "fail", "cancelled")
STATUS_DONE = {"success": "done", "fail": "failed", "cancelled": "cancelled"}
FORBIDDEN_RESULT_KEYS = {
    "password", "passwd", "pwd", "credential", "credentials",
    "token", "secret", "api_key", "apikey", "auth", "authorization",
}
# Shared exchange group (module 7, same model as ticketlib.py): both
# the AI user and the human are members, so request files created by
# either side stay usable by the other. Overridable for tests.
SHARED_GROUP_DEFAULT = "dclawos-session"


def shared_group():
    return os.environ.get("DCLAWOS_SHARED_GROUP", SHARED_GROUP_DEFAULT)


def set_shared_group(path):
    """Best-effort chgrp to the exchange group (skipped if absent)."""
    try:
        os.chown(path, -1, grp.getgrnam(shared_group()).gr_gid)
    except (KeyError, OSError):
        pass


def ensure_dir(path):
    """Create an exchange dir: shared group + 0770 (best effort)."""
    os.makedirs(path, exist_ok=True)
    set_shared_group(path)
    try:
        os.chmod(path, 0o770)
    except OSError:
        pass


def takeover_dir():
    return os.environ.get("DCLAWOS_TAKEOVER_DIR", TAKEOVER_DIR_DEFAULT)


def request_path(request_id):
    if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
        raise ValueError(f"invalid takeover request id: {request_id!r}")
    return os.path.join(takeover_dir(), request_id + ".json")


def _atomic_write(path, text):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    set_shared_group(tmp)
    try:
        os.chmod(tmp, 0o660)
    except OSError:
        pass
    os.replace(tmp, path)


def new_request(kind, message, app_hint):
    now = int(time.time())
    return {
        "id": secrets.token_hex(8),
        "kind": kind,
        "message": message,
        "app_hint": app_hint,
        "status": "pending",
        "created_at": now,
        "completed_at": None,
        "result": None,
    }


def save_request(request):
    _atomic_write(
        request_path(request["id"]),
        json.dumps(request, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n")


def load_request(request_id):
    with open(request_path(request_id), "r", encoding="utf-8") as handle:
        request = json.load(handle)
    if not isinstance(request, dict) or request.get("id") != request_id:
        raise ValueError(f"request file mismatch for id: {request_id!r}")
    return request


def list_requests():
    try:
        names = sorted(os.listdir(takeover_dir()))
    except OSError:
        return []
    requests = []
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            requests.append(load_request(name[:-5]))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return requests


def count_pending():
    return sum(1 for r in list_requests() if r.get("status") == "pending")


def write_result(request_id, outcome, session_handle, extra=None):
    """Resolve a pending request. Returns the updated request dict.

    outcome: success | fail | cancelled. extra fields are filtered:
    any key matching FORBIDDEN_RESULT_KEYS (case-insensitive) is
    dropped, so no caller can smuggle a credential into the backfill.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome!r}")
    request = load_request(request_id)
    if request.get("status") != "pending":
        raise ValueError(
            f"cannot resolve request in status "
            f"{request.get('status')!r}")
    result = {"outcome": outcome, "session_handle": session_handle or None}
    for key, value in (extra or {}).items():
        if str(key).lower() in FORBIDDEN_RESULT_KEYS:
            continue
        result[str(key)] = value
    request["status"] = STATUS_DONE[outcome]
    request["completed_at"] = int(time.time())
    request["result"] = result
    save_request(request)
    return request


def wait_for_result(request_id, timeout_seconds, poll_seconds=0.5):
    """Block until the request leaves pending or the timeout hits.

    Returns the request dict, or None on timeout. Never raises on a
    missing file after the first load: a deleted request is returned
    as None (the agent treats it as gone, not as success).
    """
    deadline = time.time() + timeout_seconds
    while True:
        try:
            request = load_request(request_id)
        except (ValueError, OSError, json.JSONDecodeError):
            return None
        if request.get("status") != "pending":
            return request
        if time.time() >= deadline:
            return None
        time.sleep(min(poll_seconds, max(0.0, deadline - time.time())))

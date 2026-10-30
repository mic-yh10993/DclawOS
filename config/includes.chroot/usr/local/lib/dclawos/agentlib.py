#!/usr/bin/env python3
"""Shared agent-service library (Phase 3 module 1). Standard library only.

Used by dclawos-agent (the service daemon), dclawos-approve (the human
approval popup) and dclawos-secret (the secret admin CLI). No GTK here:
this module must stay importable in headless tests.

Concepts (evaluation cards §5/§6/§7/§9, user-confirmed 2026-10-29/30):
- Task: /var/lib/dclawos/tasks/<16-hex-id>.json
  {id, title, goal, status, session, parent, steps, current,
   created_at, updated_at, result, error}
  Status: queued | running | done | failed | cancelled.
  A task with a parent id is a SUBAGENT session: the same service loop
  runs it and waits for it (sequential; no parallel execution yet).
- Approval tiers (the privilege model; module 4 adds the full-access
  GUI toggle, this module reads it):
  - tier 0 (free): read-only tools inside the allowed scope.
  - tier 1 (ticket + human approval): writes inside the agent
    workspace, shell commands, service restarts, package installs,
    mutating MCP tool calls.
  - tier 2 (ticket + explicit approval, never silent): deleting a
    file, any path outside the agent workspace (including the human
    home /home/dclaw, which the agent user cannot traverse anyway).
  - full-access mode (GSettings org.dclawos.settings agent-full-access,
    default false): tiers 1-2 are auto-approved but STILL ticketed and
    logged for audit. Full access never skips the audit trail.
- Approval flow: the service creates a ticket (ticketlib), then polls
  its status until approved/denied/expired/timeout. The human approves
  in dclawos-approve; the CLI approve command stays test-only.
- Events: JSON lines appended to /var/lib/dclawos/events.jsonl in the
  exact format dclawos-log-event writes (ts, type, summary, session).
- Secrets: /var/lib/dclawos/secrets/<name> (0600, dclaw-agent owned,
  written only via the broker write-secret op). Values are NEVER
  logged, NEVER put in events, NEVER echoed. read_secret is for the
  agent service itself (it owns the files); there is deliberately no
  CLI get path.

Reuse notes (evaluation cards, step 0 retrieval):
- Ticket-gated approval follows the OpenClaw gateway approval PATTERN
  (approve-before-execute, per-operation ticket with expiry); the
  transport (Unix socket + JSON ticket files) is DclawOS-written.
// reuse-check: openclaw approval/HTTP flow NOT copied, reason: the
  gateway's approval is an HTTP service with its own auth model; the
  DclawOS broker is a local root daemon, so only the
  approve-before-execute pattern transfers, not the transport.
// reuse-check: openclaw agent-step loop NOT copied, reason: the
  gateway loop assumes an LLM + gateway session; DclawOS steps are
  explicit tool steps from a task file, so the loop is DclawOS-written
  and only the session/step vocabulary transfers.
"""

import grp
import json
import os
import re
import secrets
import subprocess
import time

TASKS_DIR_DEFAULT = "/var/lib/dclawos/tasks"
EVENTS_PATH_DEFAULT = "/var/lib/dclawos/events.jsonl"
SECRETS_DIR_DEFAULT = "/var/lib/dclawos/secrets"
STOP_FLAG_DEFAULT = "/var/lib/dclawos/agent-stop.json"
AGENT_WORKSPACE_DEFAULT = "/home/dclaw-agent/workspace"
AGENT_USER = "dclaw-agent"
SHARED_GROUP_DEFAULT = "dclawos-session"
TASK_ID_RE = re.compile(r"^[0-9a-f]{16}$")
SECRET_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
TASK_STATUSES = ("queued", "running", "done", "failed", "cancelled")

# Tools the service dispatches. New tools must be added here AND in
# dclawos-agent's dispatch table, with a classify() entry below.
TOOLS = (
    "shell", "read_file", "write_file", "list_files",
    "service_status", "service_restart", "package_install",
    "route_decide", "mcp_call", "skill_run", "subagent",
    "takeover_request",
)


def shared_group():
    return os.environ.get("DCLAWOS_SHARED_GROUP", SHARED_GROUP_DEFAULT)


def tasks_dir():
    return os.environ.get("DCLAWOS_TASKS_DIR", TASKS_DIR_DEFAULT)


def events_path():
    return os.environ.get("DCLAWOS_EVENTS_PATH", EVENTS_PATH_DEFAULT)


def secrets_dir():
    return os.environ.get("DCLAWOS_SECRETS_DIR", SECRETS_DIR_DEFAULT)


def stop_flag_path():
    return os.environ.get("DCLAWOS_STOP_FLAG", STOP_FLAG_DEFAULT)


def agent_workspace():
    return os.environ.get("DCLAWOS_AGENT_WORKSPACE",
                         AGENT_WORKSPACE_DEFAULT)


def set_shared_group(path):
    try:
        os.chown(path, -1, grp.getgrnam(shared_group()).gr_gid)
    except (KeyError, OSError):
        pass


def ensure_exchange_dir(path):
    os.makedirs(path, exist_ok=True)
    set_shared_group(path)
    try:
        os.chmod(path, 0o770)
    except OSError:
        pass


def atomic_write(path, text, mode=0o660):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    set_shared_group(tmp)
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def log_event(event_type, summary, session=""):
    """Append one event line. Never call with secret values."""
    event = {"ts": int(time.time()), "type": event_type,
             "summary": summary, "session": session}
    path = events_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        set_shared_group(path)
        try:
            os.chmod(path, 0o660)
        except OSError:
            pass
    except OSError:
        pass


def stop_requested():
    return os.path.exists(stop_flag_path())


# ---------------------------------------------------------------- tasks

def task_path(task_id):
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        raise ValueError(f"invalid task id: {task_id!r}")
    return os.path.join(tasks_dir(), task_id + ".json")


def new_task(title, goal, steps, parent=""):
    now = int(time.time())
    return {
        "id": secrets.token_hex(8),
        "title": title or "untitled",
        "goal": goal or "",
        "status": "queued",
        "session": secrets.token_hex(8),
        "parent": parent or "",
        "steps": steps or [],
        "current": 0,
        "created_at": now,
        "updated_at": now,
        "result": None,
        "error": None,
    }


def save_task(task):
    ensure_exchange_dir(tasks_dir())
    task["updated_at"] = int(time.time())
    atomic_write(task_path(task["id"]),
                 json.dumps(task, indent=2, sort_keys=True) + "\n")


def load_task(task_id):
    with open(task_path(task_id), "r", encoding="utf-8") as handle:
        task = json.load(handle)
    if not isinstance(task, dict) or task.get("id") != task_id:
        raise ValueError(f"task file mismatch for id: {task_id!r}")
    return task


def list_tasks(status=None):
    try:
        names = os.listdir(tasks_dir())
    except OSError:
        return []
    out = []
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        try:
            task = load_task(name[:-5])
        except (OSError, ValueError):
            continue
        if status is None or task.get("status") == status:
            out.append(task)
    out.sort(key=lambda t: t.get("created_at", 0))
    return out


def next_queued_task():
    queued = list_tasks("queued")
    return queued[0] if queued else None


# ------------------------------------------------------------- approval

def full_access_enabled():
    """Read the full-access toggle (module 4 GUI writes it).

    Via the gsettings CLI so this module stays gi-free. Any failure
    (missing schema, missing binary) means full access OFF.
    """
    try:
        proc = subprocess.run(
            ["gsettings", "get", "org.dclawos.settings",
             "agent-full-access"],
            capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return proc.stdout.strip() == "true"


def _is_under(path, prefix):
    return path == prefix or path.startswith(prefix + "/")


def classify(tool, params):
    """Return (tier, reason). Tier 0 needs no ticket; 1-2 need one.

    Unknown tools are tier 2 (fail closed): the daemon refuses them
    before they reach any dispatch table.
    """
    if not isinstance(params, dict):
        return 2, "params must be an object"
    if tool in ("route_decide", "service_status"):
        return 0, "read-only"
    if tool == "list_files":
        path = params.get("path", agent_workspace())
        if not isinstance(path, str) or not path:
            return 2, "list_files needs a path"
        if _is_under(os.path.realpath(path), agent_workspace()):
            return 0, "read-only inside workspace"
        return 2, "list outside agent workspace"
    if tool == "read_file":
        path = params.get("path")
        if not isinstance(path, str) or not path:
            return 2, "read_file needs a path"
        real = os.path.realpath(path)
        if _is_under(real, agent_workspace()) or \
                _is_under(real, "/var/lib/dclawos") or \
                _is_under(real, "/etc/dclawos"):
            return 0, "read-only inside allowed scope"
        return 2, "read outside agent workspace"
    if tool == "write_file":
        path = params.get("path")
        if not isinstance(path, str) or not path:
            return 2, "write_file needs a path"
        if _is_under(os.path.realpath(path), agent_workspace()):
            return 1, "write inside workspace"
        return 2, "write outside agent workspace"
    if tool == "shell":
        return 1, "shell commands are mutating by default"
    if tool in ("service_restart", "package_install"):
        return 1, "system mutation via broker"
    if tool in ("mcp_call", "skill_run"):
        return 1, "third-party tool execution"
    if tool == "subagent":
        return 1, "child session execution"
    if tool == "takeover_request":
        return 0, "request only; the human act itself is out of band"
    return 2, f"unknown tool: {tool!r}"


def request_approval(tool, params, summary, ttl_seconds=600):
    """Create a ticket and wait for the human verdict.

    Returns (verdict, ticket_id): verdict is approved | denied |
    expired | timeout. Full-access mode auto-approves but still
    creates the ticket so the audit trail is complete.
    """
    import ticketlib  # local import: ticketlib lives beside this module
    ticket = ticketlib.new_ticket(tool, params, summary, ttl_seconds)
    ticketlib.ensure_dir(ticketlib.tickets_dir())
    ticketlib.save_ticket(ticket)
    if full_access_enabled():
        ticket["status"] = "approved"
        ticket["approved_by"] = "full-access-mode"
        ticket["approved_at"] = int(time.time())
        ticketlib.save_ticket(ticket)
        log_event("approval", f"auto-approved (full-access): {summary}")
        return "approved", ticket["id"]
    log_event("approval", f"approval requested: {summary}")
    deadline = time.time() + ttl_seconds
    while time.time() < deadline:
        if stop_requested():
            return "cancelled", ticket["id"]
        try:
            current = ticketlib.load_ticket(ticket["id"])
        except (OSError, ValueError):
            return "expired", ticket["id"]
        status = current.get("status")
        if status == "approved":
            log_event("approval", f"approved: {summary}")
            return "approved", ticket["id"]
        if status == "denied":
            log_event("approval", f"denied: {summary}")
            return "denied", ticket["id"]
        if status in ("consumed", "expired") or \
                ticketlib.is_expired(current):
            return "expired", ticket["id"]
        time.sleep(2)
    return "timeout", ticket["id"]


# -------------------------------------------------------------- secrets

def read_secret(name):
    """Read an agent-owned secret file. Raises on any problem.

    The value is returned to the caller only; callers must never log
    it, put it in events, or include it in ticket summaries.
    """
    if not isinstance(name, str) or not SECRET_NAME_RE.match(name):
        raise ValueError(f"invalid secret name: {name!r}")
    path = os.path.join(secrets_dir(), name)
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().strip()

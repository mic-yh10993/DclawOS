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
  - full-access mode (shared settings file key agent-full-access,
    default false; settingslib — the GUI and this service read the
    SAME file since P5-M7, user decision 2026-10-31 option B):
    tiers 1-2 are auto-approved but STILL ticketed and logged for
    audit. Full access never skips the audit trail.
- Approval flow: the service creates a ticket (ticketlib), then polls
  its status until approved/denied/expired/timeout. The human approves
  in dclawos-approve; the CLI approve command stays test-only.
- Events: JSON lines appended to /var/lib/dclawos/events.jsonl in the
  exact format dclawos-log-event writes (ts, type, summary, session).
  Phase 6 M1: log_event also publishes each event to the Unix-socket
  event bus (eventbus.py) best-effort; the file stays the durable log.
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
    "shell", "read_file", "write_file", "edit_file", "list_files",
    "process", "web_fetch",
    "service_status", "service_restart", "package_install",
    "route_decide", "mcp_call", "skill_run", "subagent",
    "takeover_request", "connector_send", "cron",
    "memory_save", "memory_search",
    # Phase 5 WS2: self-provisioning (provisioninglib).
    "registry_list", "skill_install", "skill_remove",
    "mcp_add", "mcp_remove", "channel_manage",
    # Phase 5 follow-up (P5-M7): shared settings file (settingslib).
    "settings_get", "settings_set",
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


def log_event(event_type, summary, session="", task_id=None,
              data=None):
    """Append one event line, then publish it to the event bus.

    Never call with secret values. Phase 6 M1: events.jsonl stays
    the DURABLE append log (written first); the bus publish is
    best-effort freshness only — a dead bus never blocks or loses
    the durable event. The envelope keeps ts/type/summary/session
    and adds a unique id, plus task_id/data when the caller has
    that context. Every producer that goes through this helper
    (daemon, broker, provisioning, CLIs importing agentlib) reaches
    the bus through the same path.
    """
    event = {"ts": int(time.time()), "type": event_type,
             "summary": summary, "session": session,
             "id": secrets.token_hex(8)}
    if task_id:
        event["task_id"] = task_id
    if data is not None:
        event["data"] = data
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
    try:
        import eventbus  # local: eventbus never imports agentlib
        eventbus.publish_event(event)
    except Exception:  # noqa: BLE001 - publish is best-effort
        pass
    return event


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
    # Phase 6 M1: wake the daemon / refresh monitors via the bus,
    # best-effort. This is a bus-only signal (data.display False):
    # it is NOT written to events.jsonl and UIs must not render it
    # as an event row. The file write above also fires the daemon's
    # inotify watch, so a dead bus still wakes the daemon.
    try:
        import eventbus
        eventbus.publish_event({
            "ts": int(time.time()), "type": "task",
            "summary": f"task {task['id']} {task.get('status', '')}",
            "session": task.get("session", ""),
            "task_id": task["id"],
            "data": {"task_id": task["id"],
                     "status": task.get("status"),
                     "display": False, "wake": True},
        })
    except Exception:  # noqa: BLE001 - wake is best-effort
        pass


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

    P5-M7: read from the SHARED settings file (settingslib) — the
    GUI writes the same file, so the toggle the human flips is the
    toggle this service sees. (Before, this ran the gsettings CLI
    as dclaw-agent, which reads dclaw-agent's OWN dconf database:
    always the default, i.e. the GUI toggle never reached the
    daemon.) Any failure means full access OFF.
    """
    try:
        import settingslib  # local import, same pattern as ticketlib
        return bool(settingslib.get_setting("agent-full-access"))
    except Exception:  # noqa: BLE001 - fail closed to OFF
        return False


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
    if tool == "edit_file":
        path = params.get("path")
        if not isinstance(path, str) or not path:
            return 2, "edit_file needs a path"
        if _is_under(os.path.realpath(path), agent_workspace()):
            return 1, "edit inside workspace"
        return 2, "edit outside agent workspace"
    if tool == "process":
        action = params.get("action")
        if action in ("poll", "log"):
            return 0, "process inspection is read-only"
        if action in ("start", "kill"):
            return 1, "process lifecycle mutates state"
        return 2, "process needs a valid action"
    if tool == "web_fetch":
        url = params.get("url")
        if not isinstance(url, str) or not url.startswith(
                ("http://", "https://")):
            return 2, "web_fetch needs an http(s) url"
        return 0, "read-only web fetch"
    if tool == "shell":
        # WS2 audit G-21: the privilege model says deleting is
        # tier 2, but a plain shell step could reach tier 1 with
        # rm. The common delete argv shapes are tier 2 here; this
        # is a guard on the obvious path, not a shell parser.
        argv = params.get("argv")
        if isinstance(argv, list) and argv and \
                isinstance(argv[0], str) and \
                os.path.basename(argv[0]) in (
                    "rm", "rmdir", "unlink", "shred"):
            return 2, "shell delete commands are tier 2"
        return 1, "shell commands are mutating by default"
    if tool == "registry_list":
        return 0, "registry discovery is read-only (env redacted)"
    if tool in ("skill_install", "skill_remove", "mcp_add",
                "mcp_remove"):
        return 2, "self-provisioning defines future code execution"
    if tool == "channel_manage":
        if params.get("action") == "list":
            return 0, "listing channels is read-only"
        return 2, "channel management arms a message ingress path"
    if tool == "settings_get":
        return 0, "settings are non-secret config; read is free"
    if tool == "settings_set":
        # Tier 2 even though some keys are cosmetic: the same tool
        # can repoint the model provider. Human-only keys are NOT a
        # tier question — settingslib.AGENT_DENIED_KEYS refuses them
        # before any ticket exists (daemon execute_step + the lib).
        return 2, "changing shared system settings mutates state"
    if tool in ("service_restart", "package_install"):
        return 1, "system mutation via broker"
    if tool in ("mcp_call", "skill_run", "connector_send"):
        return 1, "third-party tool execution"
    if tool == "subagent":
        return 1, "child session execution"
    if tool == "memory_search":
        return 0, "memory search is read-only"
    if tool == "memory_save":
        return 1, "memory writes persist across sessions"
    if tool == "cron":
        if params.get("action") == "list":
            return 0, "listing scheduled jobs is read-only"
        return 1, "changing scheduled jobs mutates state"
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


def broker_execute(ticket_id, operation, params):
    """Send one ticketed operation to the privileged broker.

    Shared by the agent daemon and GUI callers (settings secrets page):
    the caller owns the approval (ticket already approved); this only
    performs the socket round-trip and returns the broker reply dict.
    Values passed as params are never logged here.
    """
    import socket
    import ticketlib  # local import: ticketlib lives beside this module
    request = json.dumps({"ticket_id": ticket_id, "operation": operation,
                          "params": params}) + "\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(60)
        sock.connect(ticketlib.socket_path())
        sock.sendall(request.encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "error": "bad broker reply"}


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

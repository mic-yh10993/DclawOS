#!/usr/bin/env python3
"""Shared ticket store for the DclawOS privilege broker.

Used by dclawos-privileged (broker), dclawos-ticket (CLI) and the future
approval GUI (module 6). Standard library only.

Ticket file: /run/dclawos/tickets/<16-hex-id>.json (tmpfs: a reboot
clears all tickets, so no stale approval survives a reboot):
  {id, operation, params_sha256, params_summary, status,
   created_at, expires_at, approved_by, approved_at,
   executed_at, result}
Status: pending | approved | denied | consumed. Expiry is computed by
readers from expires_at; the broker flips an expired ticket's status to
"expired" when an execute attempt is refused for audit.

Security notes (read before extending):
- Ticket ids MUST match ^[0-9a-f]{16}$ (secrets.token_hex(8)); any other
  id is rejected before it touches the filesystem (path traversal).
- Ticket files hold params SUMMARIES, never secrets: approval UIs and
  logs show the summary. If a future operation needs a secret parameter,
  it must NOT go through this store.
- Unix separation is NOT the approval boundary on this single-user
  system (the AI and the human share user dclaw): the boundary is
  human-in-the-loop approval (module 6 GUI popup). The CLI approve
  command is test-only and says so on every use. Module 7 (separate AI
  user without sudo) makes the broker the only privileged path.
"""

import hashlib
import json
import os
import re
import secrets
import time

TICKETS_DIR_DEFAULT = "/run/dclawos/tickets"
SOCKET_PATH_DEFAULT = "/run/dclawos/privileged.sock"
TICKET_ID_RE = re.compile(r"^[0-9a-f]{16}$")


def tickets_dir():
    return os.environ.get("DCLAWOS_TICKETS_DIR", TICKETS_DIR_DEFAULT)


def socket_path():
    return os.environ.get("DCLAWOS_SOCKET_PATH", SOCKET_PATH_DEFAULT)


def params_sha256(params):
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ticket_path(ticket_id):
    if not isinstance(ticket_id, str) or not TICKET_ID_RE.match(ticket_id):
        raise ValueError(f"invalid ticket id: {ticket_id!r}")
    return os.path.join(tickets_dir(), ticket_id + ".json")


def atomic_write(path, text):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        os.chmod(tmp, 0o660)
    except OSError:
        pass
    os.replace(tmp, path)


def new_ticket(operation, params, summary, ttl_seconds):
    now = int(time.time())
    return {
        "id": secrets.token_hex(8),
        "operation": operation,
        "params_sha256": params_sha256(params),
        "params_summary": summary,
        "status": "pending",
        "created_at": now,
        "expires_at": now + int(ttl_seconds),
        "approved_by": None,
        "approved_at": None,
        "executed_at": None,
        "result": None,
    }


def save_ticket(ticket):
    atomic_write(
        ticket_path(ticket["id"]),
        json.dumps(ticket, indent=2, sort_keys=True) + "\n")


def load_ticket(ticket_id):
    with open(ticket_path(ticket_id), "r", encoding="utf-8") as handle:
        ticket = json.load(handle)
    if not isinstance(ticket, dict) or ticket.get("id") != ticket_id:
        raise ValueError(f"ticket file mismatch for id: {ticket_id!r}")
    return ticket


def is_expired(ticket, now=None):
    if now is None:
        now = int(time.time())
    return now >= int(ticket.get("expires_at", 0))


def validate_for_execution(ticket, operation, params):
    """Return (ok, error). The broker refuses unless ALL hold."""
    if ticket.get("status") != "approved":
        return False, f"ticket status is {ticket.get('status')!r}, not approved"
    if is_expired(ticket):
        return False, "ticket expired"
    if ticket.get("operation") != operation:
        return False, (
            f"ticket operation {ticket.get('operation')!r} does not match "
            f"requested {operation!r}")
    if ticket.get("params_sha256") != params_sha256(params):
        return False, "ticket params do not match the approved params"
    return True, None

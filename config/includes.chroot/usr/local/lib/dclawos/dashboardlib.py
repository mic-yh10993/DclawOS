#!/usr/bin/env python3
"""Dashboard host logic + taskbar agent-state reducer (Phase 6 M4).

Headless, stdlib + local libs — the same split as M3: the
dclawos-dashboard GTK/WebKit window is a thin shell around
DashboardHost here, so the whole bridge surface, the snapshot /
live seam and the shell state reducer can be gate-tested
headlessly (dashboardtest.py) without a display. The page is
untrusted; every verb arrives already validated by
bridgeprotocol and lands in dispatch().

What lives here, and where it came from:
- History: the durable events file (agentlib.events_path(),
  events.jsonl) is read ONCE at host construction — the pre-M4
  dashboard's load_initial_events semantics. After that, freshness
  is bus events only; the file is never re-read.
- Snapshot/live seam: every durable event carries a unique id
  (agentlib.log_event). The host seeds an EventDeduper with the
  history ids, so a live event that is already in the snapshot is
  dropped, and snapshot + live merge (merge_events) never repeats
  or reorders: history order is file order, live events append.
  Events from producers that predate ids are fingerprinted on
  (ts, type, summary, session, task_id) instead.
- Counts: computed from agentlib.list_tasks (statuses queued /
  running / done / failed / cancelled) and usagelib
  .aggregate_usage (M2 ledger; known cost and unknown-cost count
  stay separate — a null cost is never shown as 0). Counts are
  recomputed by the HOST when a relevant bus event arrives and
  pushed to the page; neither the page nor the host polls.
- Approvals: cards are built ONLY from (a) the approval bus
  event's structured data (tool / tier / summary / params,
  redacted by agentlib before publishing, re-redacted by the
  shared chatlib.approval_card_view) and (b) the ticket store
  itself (operation + params_summary from ticketlib; ticket
  files hold NO raw params — params_sha256 + summary only — so a
  store-seeded card honestly carries params {}). Verdicts go
  through chatlib.apply_verdict, the ONE shared write extracted
  in M4 from the chat host (itself a field-for-field copy of
  dclawos-approve). There is no second verdict channel and no
  task-scoped grant: ticketlib is a per-operation single-ticket
  model with no task-level authorisation carrier, so the
  overlay offers approve/deny only (see docs/PHASE6.md M4:
  scoped approval is a queued item, not a shipped feature).
- Takeovers: pending requests come from takeoverlib; the banner
  count is initialised ONCE from the store and then maintained
  from bus takeover events (takeoverlib.save_request publishes
  one per create/resolve) — never re-counted on a timer.
- Shell reducer: pure functions (new_shell_state /
  reduce_shell_state / shell_status) reducing bus events to the
  taskbar's four agent states. The taskbar (dclawos-shell) seeds
  its state ONCE via initial_shell_state() and afterwards only
  reduces events; the reducer's sets make re-delivery idempotent
  and let the state fall back level by level as items clear.

// reuse-check: host/bridge split, bus filtering and card views
  ADOPTED from chatlib (M3) — approval_card_from_event /
  approval_card_view / list_pending_tickets / apply_verdict are
  imported, not copied; the M4 extraction moved them to module
  level in chatlib precisely so this host shares them.
// reuse-check: counts/usage ADOPTED from agentlib.list_tasks and
  usagelib.aggregate_usage (M2); takeover reads ADOPTED from
  takeoverlib.list_requests/count_pending. No parallel store.
// reuse-check: event envelope + dedupe key ADOPTED from
  agentlib.log_event (unique id per event); the durable file
  stays the only history source, exactly as the pre-M4 GTK
  dashboard treated it.
"""

import json
import os
import subprocess

import agentlib
import bridgeprotocol
import chatlib
import takeoverlib
import usagelib

MAX_EVENTS = 500

LAUNCH_TARGETS = {
    "dashboard": "dclawos-dashboard",
    "monitor": "dclawos-monitor",
    "settings": "dclawos-settings",
    "chat": "dclawos-chat",
    "takeover": "dclawos-takeover",
    "editor": "dclawos-editor",
    "approve": "dclawos-approve",
}

TYPE_LABELS = {
    "command": "命令",
    "output": "输出",
    "file": "文件",
    "tokens": "tokens",
    "model": "模型",
    "approval": "审批",
    "takeover": "接管",
    "system": "系统",
    "llm": "模型",
    "task": "任务",
}

# Event types whose arrival can change the counts / usage / shell
# state the page shows; the host recomputes and pushes on these.
COUNT_EVENT_TYPES = frozenset({
    "task", "system", "llm", "approval", "takeover",
    "command", "output",
})


# ---------------------------------------------------------------- history

def read_history(limit=MAX_EVENTS):
    """One-time history load: parse the durable events file in
    file order (oldest first), keeping the newest `limit` rows.
    Missing/corrupt lines are skipped, never fatal."""
    events = []
    try:
        with open(agentlib.events_path(), "r",
                  encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return events
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type"):
            events.append(event)
    return events[-limit:] if limit else events


def event_key(event):
    """Dedupe key for one event: its unique envelope id when it
    has one, else a fingerprint of the durable fields."""
    if not isinstance(event, dict):
        return None
    event_id = event.get("id")
    if isinstance(event_id, str) and event_id:
        return "id:" + event_id
    return "fp:" + json.dumps(
        [event.get("ts"), event.get("type"), event.get("summary"),
         event.get("session"), event.get("task_id")],
        ensure_ascii=False, sort_keys=True)


class EventDeduper:
    """Seen-set over event keys: add() returns True exactly once
    per distinct event (the snapshot/live seam primitive)."""

    def __init__(self, events=()):
        self._seen = set()
        for event in events:
            key = event_key(event)
            if key is not None:
                self._seen.add(key)

    def add(self, event):
        key = event_key(event)
        if key is None or key in self._seen:
            return False
        self._seen.add(key)
        return True

    def __contains__(self, event):
        key = event_key(event)
        return key is not None and key in self._seen


def merge_events(snapshot, live, limit=MAX_EVENTS):
    """Merge a history snapshot with live events: snapshot order
    first (file order), then live events in arrival order, each
    distinct event exactly once (by envelope id / fingerprint).
    Neither input is mutated."""
    deduper = EventDeduper()
    merged = []
    for event in list(snapshot) + list(live):
        if deduper.add(event):
            merged.append(event)
    return merged[-limit:] if limit else merged


# ---------------------------------------------------------------- counts

def compute_counts(tasks=None):
    """Task counts from the task store. `waiting` is the queued
    status (the page label 等待); cancelled is reported
    separately and never folded into failed."""
    if tasks is None:
        tasks = agentlib.list_tasks()
    counts = {"running": 0, "waiting": 0, "done": 0, "failed": 0,
              "cancelled": 0, "total": 0}
    for task in tasks:
        counts["total"] += 1
        status = task.get("status")
        if status == "running":
            counts["running"] += 1
        elif status == "queued":
            counts["waiting"] += 1
        elif status == "done":
            counts["done"] += 1
        elif status == "failed":
            counts["failed"] += 1
        elif status == "cancelled":
            counts["cancelled"] += 1
    return counts


def usage_view(records=None):
    """Usage totals for the page, usagelib semantics verbatim:
    known cost summed, null-cost calls counted separately as
    unknown — never coerced to 0."""
    agg = usagelib.aggregate_usage(records)
    return {
        "total_calls": agg["total_calls"],
        "prompt_tokens": agg["prompt_tokens"],
        "completion_tokens": agg["completion_tokens"],
        "total_tokens": agg["prompt_tokens"] + agg["completion_tokens"],
        "known_cost_usd": agg["known_cost_usd"],
        "unknown_cost_count": agg["unknown_cost_count"],
    }


def pending_takeover_views():
    out = []
    for request in takeoverlib.list_requests():
        if request.get("status") == "pending":
            out.append({
                "id": request.get("id"),
                "kind": request.get("kind", ""),
                "message": request.get("message", ""),
                "app_hint": request.get("app_hint", ""),
                "created_at": request.get("created_at", 0),
            })
    return out


def _seed_cards():
    """Approval cards seeded from the ticket store (host start).
    Ticket files hold operation + params_summary + params hash
    only, so params is honestly {} here — raw params exist only
    on the approval event that announced the ticket."""
    cards = {}
    for ticket in chatlib.list_pending_tickets():
        cards[ticket["id"]] = {
            "ticket_id": ticket["id"],
            "tool": ticket.get("operation", ""),
            "tier": None,
            "summary": ticket.get("params_summary", ""),
            "params": {},
            "status": "pending",
        }
    return cards


# ------------------------------------------------------- shell reducer
# Pure functions: state is a plain dict of sorted lists so it can
# be compared/serialised in tests. Sets live inside the helpers.
#
# Four states, by priority (highest first):
#   takeovers pending  -> attention 需要你
#   approvals pending  -> approval  等待批准
#   tasks/llm active   -> thinking  思考中
#   nothing            -> idle      空闲
# Clearing the top set falls back to the next level, never
# straight to idle while lower work remains.

SHELL_LABELS = {
    "idle": "空闲",
    "thinking": "思考中",
    "approval": "等待批准",
    "attention": "需要你",
}

_TERMINAL_SYSTEM_PREFIXES = (
    "task done:", "llm task done", "task failed",
    "task cancelled", "llm call failed", "llm task failed",
)


def new_shell_state(active=(), approvals=(), takeovers=()):
    return {
        "active": sorted(set(active)),
        "approvals": sorted(set(approvals)),
        "takeovers": sorted(set(takeovers)),
    }


def initial_shell_state():
    """Seed the reducer ONCE from the stores: pending ticket ids
    (chatlib.list_pending_tickets), pending takeover ids
    (takeoverlib) and currently-running task ids (agentlib).
    After this, state moves ONLY by reducing bus events — the
    stores are not re-read periodically."""
    approvals = [t["id"] for t in chatlib.list_pending_tickets()]
    takeovers = [r["id"] for r in takeoverlib.list_requests()
                 if r.get("status") == "pending"]
    active = [t["id"] for t in agentlib.list_tasks("running")]
    return new_shell_state(active=active, approvals=approvals,
                           takeovers=takeovers)


def _event_task_key(event):
    data = event.get("data") if isinstance(event.get("data"), dict) \
        else {}
    return event.get("task_id") or data.get("task_id") \
        or event.get("session") or None


def reduce_shell_state(state, event):
    """Reduce ONE bus event into a NEW shell state dict.

    - task events (agentlib.save_task signals): status queued /
      running marks the task active; done / failed / cancelled
      clears it.
    - llm / command / output events mark their task (or session)
      active; the matching terminal system event or task signal
      clears it.
    - approval events: a request (no verdict) adds the ticket;
      a verdict (approved / denied) — including the host's own
      synthetic verdict event — removes it. Auto-approved events
      carry verdict=approved and never enter the set.
    - takeover events (takeoverlib.save_request): status pending
      adds the request; done / failed / cancelled removes it.
    Unknown types change nothing. The input is never mutated.
    """
    active = set(state.get("active", ()))
    approvals = set(state.get("approvals", ()))
    takeovers = set(state.get("takeovers", ()))
    if isinstance(event, dict):
        etype = event.get("type", "")
        data = event.get("data") if isinstance(
            event.get("data"), dict) else {}
        if etype == "task":
            key = _event_task_key(event)
            status = data.get("status") or event.get("status")
            if key:
                if status in ("queued", "running"):
                    active.add(key)
                elif status in ("done", "failed", "cancelled"):
                    active.discard(key)
        elif etype in ("llm", "command", "output"):
            key = _event_task_key(event)
            if key:
                active.add(key)
        elif etype == "system":
            summary = str(event.get("summary", ""))
            if summary.startswith(_TERMINAL_SYSTEM_PREFIXES):
                key = _event_task_key(event)
                if key:
                    active.discard(key)
        elif etype == "approval":
            ticket_id = data.get("ticket_id")
            if isinstance(ticket_id, str) and ticket_id:
                if data.get("verdict") in ("approved", "denied"):
                    approvals.discard(ticket_id)
                elif "verdict" not in data:
                    approvals.add(ticket_id)
        elif etype == "takeover":
            request_id = data.get("request_id")
            if isinstance(request_id, str) and request_id:
                status = data.get("status")
                if status == "pending":
                    takeovers.add(request_id)
                elif status in ("done", "failed", "cancelled"):
                    takeovers.discard(request_id)
    return new_shell_state(active=active, approvals=approvals,
                           takeovers=takeovers)


def shell_status(state):
    """The taskbar's four-state view of a reducer state."""
    takeovers = len(state.get("takeovers", ()))
    approvals = len(state.get("approvals", ()))
    active = len(state.get("active", ()))
    if takeovers:
        key = "attention"
    elif approvals:
        key = "approval"
    elif active:
        key = "thinking"
    else:
        key = "idle"
    return {"key": key, "label": SHELL_LABELS[key],
            "active": active, "approvals": approvals,
            "takeovers": takeovers}


# ---------------------------------------------------------------- host

class DashboardHost:
    """Typed bridge handlers + bus-event handling for one
    dashboard window. launch_fn is injectable (the GTK shell
    supplies Popen; tests record instead)."""

    def __init__(self, launch_fn=None):
        self._launch_fn = launch_fn
        self.launched = []
        self.history = read_history()
        self.deduper = EventDeduper(self.history)
        self.approvals = _seed_cards()
        self.shell = initial_shell_state()
        # The seeded shell sets come from the same stores as the
        # cards above; keep them consistent even if a store moved
        # between the two reads.
        self.shell = new_shell_state(
            active=self.shell["active"],
            approvals=sorted(self.approvals),
            takeovers=self.shell["takeovers"])

    # ------------------------------------------------------------ state

    def counts_view(self):
        return {
            "counts": compute_counts(),
            "usage": usage_view(),
            "shell": shell_status(self.shell),
            "takeovers": pending_takeover_views(),
            "approvals": [chatlib.approval_card_view(c)
                          for c in self.approvals.values()
                          if c.get("status") == "pending"],
        }

    def snapshot(self):
        view = self.counts_view()
        view["events"] = [bridgeprotocol.strip_secrets(e)
                          for e in self.history]
        view["max_events"] = MAX_EVENTS
        return view

    # --------------------------------------------------------- dispatch

    def dispatch(self, verb, params):
        params = params or {}
        if verb == "ping":
            return {"pong": True}
        if verb == "hello":
            return bridgeprotocol.make_hello_ack("dashboard")
        if verb == "dashboard.state":
            return self.snapshot()
        if verb in ("dashboard.approve", "dashboard.deny"):
            verdict = "approved" if verb == "dashboard.approve" \
                else "denied"
            result = chatlib.apply_verdict(
                params.get("ticket_id"), verdict)
            card = self.approvals.get(result["ticket_id"])
            if card is not None:
                card["status"] = verdict
            self.shell = reduce_shell_state(self.shell, {
                "type": "approval",
                "data": {"ticket_id": result["ticket_id"],
                         "verdict": verdict}})
            return result
        if verb == "dashboard.launch":
            target = params.get("target")
            command = LAUNCH_TARGETS.get(target)
            if command is None:
                raise ValueError(f"未知启动目标：{target!r}")
            try:
                if self._launch_fn is not None:
                    self._launch_fn([command])
                else:
                    subprocess.Popen([command])
            except OSError as exc:
                raise ValueError(f"启动 {command} 失败：{exc}")
            self.launched.append(target)
            return {"launched": target}
        raise ValueError(f"unhandled dashboard verb: {verb!r}")

    # ------------------------------------------------------------ events

    def handle_bus_event(self, event):
        """Fold one bus event into the host state; returns a list
        of bridge-event dicts {type, data} to push to the page:
        the timeline event itself (deduped, display events only),
        an approval.card item when an approval arrives or is
        decided, and a dashboard.counts item whenever counts /
        usage / shell state may have moved. Bus-only signals
        (data.display False) never become timeline rows but DO
        move the reducer and the counts — the pre-M4 dashboard's
        takeover-banner semantics, kept."""
        if not isinstance(event, dict):
            return []
        out = []
        self.shell = reduce_shell_state(self.shell, event)
        etype = event.get("type", "")
        data = event.get("data") if isinstance(
            event.get("data"), dict) else {}
        if etype == "approval":
            forwarded = self._note_approval(event)
            if forwarded is not None:
                out.append(forwarded)
        is_new = self.deduper.add(event)
        display = data.get("display") is not False
        if is_new and display:
            self.history.append(event)
            self.history = self.history[-MAX_EVENTS:]
            out.append({
                "type": etype,
                "data": bridgeprotocol.strip_secrets(event)})
        if etype in COUNT_EVENT_TYPES:
            out.append({"type": "dashboard.counts",
                        "data": self.counts_view()})
        return out

    def _note_approval(self, event):
        data = event.get("data") or {}
        ticket_id = data.get("ticket_id")
        if not isinstance(ticket_id, str):
            return None
        if data.get("verdict") in ("approved", "denied"):
            card = self.approvals.get(ticket_id)
            if card is not None:
                card["status"] = data["verdict"]
            return {"type": "approval.card",
                    "data": {"ticket_id": ticket_id,
                             "status": data["verdict"]}}
        card = chatlib.approval_card_from_event(event)
        if card is None:
            return None
        self.approvals[ticket_id] = card
        return {"type": "approval.card",
                "data": chatlib.approval_card_view(card)}

#!/usr/bin/env python3
"""Dashboard WebView + taskbar state gate (Phase 6 module M4).

Headless; only touches a temp dir (no display, no network, no
provider calls). Verifies:
(a) Host snapshot construction from fixtures: task counts by
    status, usage aggregation with null cost kept separate
    (never 0), history order — plus the snapshot/live seam:
    merge_events and the host's deduper drop an already-seen
    event id and admit a new one exactly once.
(b) The taskbar shell-state reducer's full transition chain:
    idle -> thinking (task/llm activity) -> waiting-approval
    (approval pending outranks thinking) -> needs-you (takeover
    pending outranks approval) -> level-by-level fallback as
    each set clears, never straight past a still-pending level.
(c) Approval overlay payload: a ticket whose approval event
    carries a secret-shaped param — the serialised host payload
    (bridge reply JSON) contains NO clear-text secret, while
    the tool name and non-sensitive params stay visible; and
    dashboard.approve / dashboard.deny write exactly the fields
    dclawos-approve writes (status / approved_by / approved_at).
(d) Pending-count initialisation + event increments agree: a
    host seeded from the stores (pending ticket + pending
    takeover + running task) starts at needs-you, and bus events
    alone walk it back down through approval -> thinking ->
    idle.

Exits non-zero on the first failed assertion. Invoked by build
hook 1008 inside the image chroot; also runnable by hand.

// reuse-check: temp-env fixture pattern ADOPTED from
  chattest.py / eventbustest.py (env overrides for every store,
  fail() helper); assertions are M4-specific.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, "/usr/local/lib/dclawos")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = tempfile.mkdtemp(prefix="dclawos-dashboardtest-")
os.environ["DCLAWOS_TASKS_DIR"] = os.path.join(ROOT, "tasks")
os.environ["DCLAWOS_EVENTS_PATH"] = os.path.join(ROOT, "events.jsonl")
os.environ["DCLAWOS_EVENTBUS_SOCKET"] = os.path.join(ROOT, "bus.sock")
os.environ["DCLAWOS_SECRETS_DIR"] = os.path.join(ROOT, "secrets")
os.environ["DCLAWOS_SETTINGS_FILE"] = os.path.join(
    ROOT, "settings", "agent-settings.json")
os.environ["DCLAWOS_REGISTRY"] = os.path.join(ROOT, "registry.json")
os.environ["DCLAWOS_USAGE_FILE"] = os.path.join(ROOT, "usage.jsonl")
os.environ["DCLAWOS_CONV_DIR"] = os.path.join(ROOT, "conversations")
os.environ["DCLAWOS_TICKETS_DIR"] = os.path.join(ROOT, "tickets")
os.environ["DCLAWOS_TAKEOVER_DIR"] = os.path.join(ROOT, "takeover")
os.environ["DCLAWOS_STOP_FLAG"] = os.path.join(ROOT, "stop.json")
os.environ["XDG_CONFIG_HOME"] = os.path.join(ROOT, "xdg")
for _dir in (os.environ["DCLAWOS_SECRETS_DIR"],
             os.environ["DCLAWOS_TICKETS_DIR"],
             os.environ["DCLAWOS_TAKEOVER_DIR"]):
    os.makedirs(_dir, exist_ok=True)

import agentlib  # noqa: E402
import bridgeprotocol  # noqa: E402
import dashboardlib  # noqa: E402
import takeoverlib  # noqa: E402
import ticketlib  # noqa: E402
import usagelib  # noqa: E402


def fail(message):
    print(f"dashboardtest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def make_task(status, title):
    task = agentlib.new_task(title, "goal", [])
    task["status"] = status
    agentlib.save_task(task)
    return task


def timeline_items(pushed):
    return [item for item in pushed
            if item.get("type") not in (
                "dashboard.counts", "approval.card")]


def main():
    # ------------------------------------------------ fixtures
    running = make_task("running", "running one")
    make_task("queued", "queued one")
    make_task("queued", "queued two")
    for index in range(3):
        make_task("done", f"done {index}")
    make_task("failed", "failed one")

    usagelib.append_usage("openai", "gpt-4o-mini", 0.5,
                          usage={"prompt_tokens": 100,
                                 "completion_tokens": 50})
    usagelib.append_usage("openai", "gpt-4o-mini", None,
                          usage={"prompt_tokens": 10,
                                 "completion_tokens": 5})
    usagelib.append_usage("anthropic", "claude-x", 0.25,
                          usage={"prompt_tokens": 200,
                                 "completion_tokens": 100})

    history = [
        {"ts": 100, "type": "system", "summary": "hist one",
         "session": "s1", "id": "aaaaaaaaaaaaaaaa"},
        {"ts": 101, "type": "command", "summary": "hist two",
         "session": "s1", "id": "bbbbbbbbbbbbbbbb"},
        {"ts": 102, "type": "system", "summary": "hist three",
         "session": "s2", "id": "cccccccccccccccc"},
    ]
    with open(os.environ["DCLAWOS_EVENTS_PATH"], "w",
              encoding="utf-8") as handle:
        for event in history:
            handle.write(json.dumps(event) + "\n")
        handle.write("not json\n")

    launched = []
    host = dashboardlib.DashboardHost(
        launch_fn=lambda argv: launched.append(argv))

    # --------------------------------------- (a) snapshot + seam
    snap = host.dispatch("dashboard.state", {})
    counts = snap.get("counts") or {}
    if counts != {"running": 1, "waiting": 2, "done": 3,
                  "failed": 1, "cancelled": 0, "total": 7}:
        fail(f"snapshot counts wrong: {counts!r}")
    usage = snap.get("usage") or {}
    if usage.get("known_cost_usd") != 0.75 or \
            usage.get("unknown_cost_count") != 1:
        fail(f"usage cost split wrong (null must stay separate): "
             f"{usage!r}")
    if usage.get("total_tokens") != 465 or \
            usage.get("total_calls") != 3:
        fail(f"usage token totals wrong: {usage!r}")
    ids = [e.get("id") for e in snap.get("events", [])]
    if ids != ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb",
               "cccccccccccccccc"]:
        fail(f"snapshot history order/ids wrong: {ids!r}")

    e4 = {"ts": 103, "type": "system", "summary": "live four",
          "session": "s2", "id": "dddddddddddddddd"}
    merged = dashboardlib.merge_events(history, [history[1], e4])
    if [e["id"] for e in merged] != [
            "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb",
            "cccccccccccccccc", "dddddddddddddddd"]:
        fail(f"merge_events dedupe/order wrong: {merged!r}")
    # A live re-delivery of a snapshot event is dropped by the host.
    pushed = host.handle_bus_event(dict(history[1]))
    if timeline_items(pushed):
        fail("host forwarded a duplicate snapshot event to the page")
    pushed = host.handle_bus_event(e4)
    if len(timeline_items(pushed)) != 1:
        fail("host did not forward the new live event exactly once")
    pushed = host.handle_bus_event(e4)
    if timeline_items(pushed):
        fail("host forwarded the same live event twice")
    if not any(item.get("type") == "dashboard.counts"
               for item in host.handle_bus_event(
                   {"ts": 104, "type": "task", "summary": "t",
                    "session": "s9", "id": "eeeeeeeeeeeeeeee",
                    "data": {"task_id": "ffffffffffffffff",
                             "status": "running",
                             "display": False}})):
        fail("task signal did not produce a counts push")

    # --------------------------------------- (b) reducer chain
    state = dashboardlib.new_shell_state()
    if dashboardlib.shell_status(state)["key"] != "idle":
        fail("empty reducer state is not idle")
    task_event = {"type": "task",
                  "data": {"task_id": "1111111111111111",
                           "status": "running", "display": False}}
    state = dashboardlib.reduce_shell_state(state, task_event)
    if dashboardlib.shell_status(state)["key"] != "thinking":
        fail("task running did not yield thinking")
    state = dashboardlib.reduce_shell_state(state, {
        "type": "llm", "task_id": "1111111111111111",
        "data": {"delta": "hi"}})
    appr_event = {"type": "approval",
                  "data": {"ticket_id": "2222222222222222",
                           "tool": "shell", "tier": 1}}
    state = dashboardlib.reduce_shell_state(state, appr_event)
    if dashboardlib.shell_status(state)["key"] != "approval":
        fail("pending approval did not outrank thinking")
    # Re-delivery is idempotent (set semantics).
    state2 = dashboardlib.reduce_shell_state(state, appr_event)
    if dashboardlib.shell_status(state2)["approvals"] != 1:
        fail("approval re-delivery double-counted")
    state = dashboardlib.reduce_shell_state(state, {
        "type": "takeover",
        "data": {"request_id": "3333333333333333",
                 "status": "pending", "display": False}})
    status = dashboardlib.shell_status(state)
    if status["key"] != "attention" or status["label"] != "需要你":
        fail(f"pending takeover did not outrank approval: {status!r}")
    state = dashboardlib.reduce_shell_state(state, {
        "type": "takeover",
        "data": {"request_id": "3333333333333333",
                 "status": "done", "display": False}})
    if dashboardlib.shell_status(state)["key"] != "approval":
        fail("takeover resolution did not fall back to approval")
    state = dashboardlib.reduce_shell_state(state, {
        "type": "approval",
        "data": {"ticket_id": "2222222222222222",
                 "verdict": "denied"}})
    if dashboardlib.shell_status(state)["key"] != "thinking":
        fail("approval verdict did not fall back to thinking")
    state = dashboardlib.reduce_shell_state(state, {
        "type": "task",
        "data": {"task_id": "1111111111111111",
                 "status": "done", "display": False}})
    if dashboardlib.shell_status(state)["key"] != "idle":
        fail("task completion did not fall back to idle")
    # An auto-approved approval event never enters the pending set.
    state = dashboardlib.reduce_shell_state(
        dashboardlib.new_shell_state(), {
            "type": "approval",
            "data": {"ticket_id": "4444444444444444",
                     "verdict": "approved", "auto": True}})
    if dashboardlib.shell_status(state)["key"] != "idle":
        fail("auto-approved event wrongly pends in the reducer")
    # The reducer never mutates its input.
    before = dashboardlib.new_shell_state()
    dashboardlib.reduce_shell_state(before, task_event)
    if before != dashboardlib.new_shell_state():
        fail("reduce_shell_state mutated its input")

    # --------------------------------------- (c) approval overlay
    raw_params = {"argv": ["echo", "hi"],
                  "api_key": "supersecret-value",
                  "path": "/tmp/x"}
    ticket = ticketlib.new_ticket("shell", raw_params,
                                  "shell echo hi", 600)
    ticketlib.ensure_dir(ticketlib.tickets_dir())
    ticketlib.save_ticket(ticket)
    pushed = host.handle_bus_event({
        "ts": 200, "type": "approval",
        "summary": "approval requested: shell echo hi",
        "session": "s1", "id": "1212121212121212",
        "data": {"ticket_id": ticket["id"], "tool": "shell",
                 "tier": 1, "summary": "shell echo hi",
                 "params": raw_params}})
    cards = [item for item in pushed
             if item.get("type") == "approval.card"]
    if len(cards) != 1:
        fail(f"approval event produced no overlay card: {pushed!r}")
    blob = json.dumps(
        {"snap": host.dispatch("dashboard.state", {}),
         "card": cards[0]}, ensure_ascii=False)
    if "supersecret-value" in blob:
        fail("host payload leaks a secret param value in clear text")
    if "<redacted>" not in blob:
        fail("host payload lacks the redacted marker")
    if cards[0]["data"].get("tool") != "shell" or \
            (cards[0]["data"].get("params") or {}).get(
                "path") != "/tmp/x":
        fail(f"overlay card lost tool/visible params: {cards[0]!r}")
    out = host.dispatch("dashboard.approve",
                        {"ticket_id": ticket["id"]})
    if out.get("status") != "approved":
        fail(f"dashboard.approve result wrong: {out!r}")
    saved = ticketlib.load_ticket(ticket["id"])
    if saved.get("status") != "approved" or \
            saved.get("approved_by") != "human-gui" or \
            not isinstance(saved.get("approved_at"), int):
        fail(f"dashboard verdict fields diverge from "
             f"dclawos-approve: {saved!r}")
    if dashboardlib.shell_status(host.shell)["approvals"] != 0:
        fail("host shell state kept a decided ticket pending")
    ticket2 = ticketlib.new_ticket("shell", {"argv": ["ls"]},
                                   "shell ls", 600)
    ticketlib.save_ticket(ticket2)
    host.dispatch("dashboard.deny", {"ticket_id": ticket2["id"]})
    saved2 = ticketlib.load_ticket(ticket2["id"])
    if saved2.get("status") != "denied" or \
            saved2.get("approved_by") != "human-gui":
        fail(f"dashboard.deny fields wrong: {saved2!r}")
    # Store-seeded cards carry no fabricated params: a pending
    # ticket that never had an event shows params {} honestly.
    ticket3 = ticketlib.new_ticket("shell", {"argv": ["pwd"]},
                                   "shell pwd", 600)
    ticketlib.save_ticket(ticket3)
    host_c = dashboardlib.DashboardHost()
    seeded = [c for c in host_c.snapshot()["approvals"]
              if c["ticket_id"] == ticket3["id"]]
    if len(seeded) != 1 or seeded[0].get("params") != {} or \
            seeded[0].get("tool") != "shell":
        fail(f"store-seeded card wrong: {seeded!r}")

    # --------------------------------------- (d) init + increments
    request = takeoverlib.new_request("captcha", "need a human", "")
    takeoverlib.ensure_dir(takeoverlib.takeover_dir())
    takeoverlib.save_request(request)
    host2 = dashboardlib.DashboardHost()
    status = dashboardlib.shell_status(host2.shell)
    if status["key"] != "attention" or status["takeovers"] != 1:
        fail(f"seeded host did not start at needs-you: {status!r}")
    if ticket3["id"] not in host2.shell["approvals"]:
        fail("seeded host missed the pending ticket")
    if running["id"] not in host2.shell["active"]:
        fail("seeded host missed the running task")
    host2.handle_bus_event({
        "type": "takeover",
        "data": {"request_id": request["id"], "status": "done",
                 "display": False}})
    if dashboardlib.shell_status(host2.shell)["key"] != "approval":
        fail("host2 did not fall back to approval after takeover")
    host2.dispatch("dashboard.deny", {"ticket_id": ticket3["id"]})
    if dashboardlib.shell_status(host2.shell)["key"] != "thinking":
        fail("host2 did not fall back to thinking after verdict")
    host2.handle_bus_event({
        "type": "task",
        "data": {"task_id": running["id"], "status": "done",
                 "display": False}})
    if dashboardlib.shell_status(host2.shell)["key"] != "idle":
        fail("host2 did not fall back to idle at the end")

    # --------------------------------------- bridge + launch
    for verb in ("dashboard.state", "dashboard.approve",
                 "dashboard.deny", "dashboard.launch"):
        bridgeprotocol.validate_command(
            {"kind": "command", "protocol": 1, "id": "x1",
             "command": verb, "params": {}}, "dashboard")
        try:
            bridgeprotocol.validate_command(
                {"kind": "command", "protocol": 1, "id": "x2",
                 "command": verb, "params": {}}, "chat")
            fail(f"{verb} was allowlisted on the chat surface")
        except bridgeprotocol.BridgeError:
            pass
    try:
        bridgeprotocol.validate_command(
            {"kind": "command", "protocol": 1, "id": "x3",
             "command": "dashboard.snapshot", "params": {}},
            "dashboard")
        fail("retired M1 placeholder verb still allowlisted")
    except bridgeprotocol.BridgeError:
        pass
    out = host.dispatch("dashboard.launch", {"target": "takeover"})
    if out.get("launched") != "takeover" or \
            launched != [["dclawos-takeover"]]:
        fail(f"dashboard.launch takeover wrong: {out!r} {launched!r}")
    try:
        host.dispatch("dashboard.launch", {"target": "nope"})
        fail("dashboard.launch accepted an unknown target")
    except ValueError:
        pass

    print("dashboardtest: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Chat WebView + true-streaming gate (Phase 6 module M3). Headless.

Only touches a temp dir, plus the real dclawos-llm binary against a
local mock server (dummy keys, no external network). Verifies:
(a) True SSE streaming: a local mock SSE server (delayed chunks) +
    the REAL dclawos-llm --stream; the first delta line must arrive
    well before the result line, deltas assemble in order into the
    final reply, the result envelope carries usage and
    stream_mode="sse", and cost is priced from the mock usage.
(b) Streaming failure: a mock that 500s BEFORE any delta yields an
    honest null response with exactly one recorded attempt; and
    with no keys llmlib sends nothing and returns an honest null.
(c) llmlib/daemon wiring: the real daemon run_llm_task streams via
    llmlib.call_stream; a bus subscriber receives the llm delta
    events in order (coalesced), and the usage ledger gains exactly
    one record whose cost stays null when no usage was sent.
(d) Staged path: a claude-format target returns
    stream_mode="staged" on both its delta and result lines —
    never "sse".
(e) Approval host path: a ticketlib ticket decided through the
    chat host's approve/deny handlers ends with the same status /
    approved_by / approved_at fields dclawos-approve writes; the
    params shown in bridge replies are redacted (a secret-shaped
    value never appears in the serialised reply), and the approval
    event agentlib publishes is redacted the same way.
(f) parse_command: the transplanted slash-command parser produces
    the same step structures as the pre-M3 window for /run, /write,
    /mcp and /subagent.

Exits non-zero on the first failed assertion. Invoked by build
hook 1008 inside the image chroot; also runnable by hand (needs
the engine bundle at /usr/share/dclawos/engine/llm.js, built by
hook 1003, for the real-binary sections).

// reuse-check: mock-server + temp-env pattern ADOPTED from
  modeltest.py / eventbustest.py (local mock endpoint, dummy key,
  temp dirs for every store); the daemon-module loader is
  eventbustest's load_daemon_module pattern. Assertions are
  M3-specific.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/usr/local/lib/dclawos")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = tempfile.mkdtemp(prefix="dclawos-chattest-")
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
os.environ["DCLAWOS_STOP_FLAG"] = os.path.join(ROOT, "stop.json")
os.environ["XDG_CONFIG_HOME"] = os.path.join(ROOT, "xdg")
for _dir in (os.environ["DCLAWOS_SECRETS_DIR"],
             os.environ["DCLAWOS_TICKETS_DIR"]):
    os.makedirs(_dir, exist_ok=True)

_CANDIDATES = [
    "/usr/local/bin/dclawos-llm",
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "..", "bin", "dclawos-llm"),
]
LLM_BIN = next((c for c in _CANDIDATES if os.path.isfile(c)), None)
os.environ["DCLAWOS_LLM_BIN"] = LLM_BIN or ""

CATALOG = os.path.join(ROOT, "catalog.json")
with open(CATALOG, "w", encoding="utf-8") as handle:
    json.dump({"providers": [
        {"id": "openai", "models": [{"id": "gpt-4o-mini", "name": ""}]},
        {"id": "anthropic",
         "models": [{"id": "claude-sonnet-4-6", "name": ""}]},
    ]}, handle)
os.environ["DCLAWOS_ENGINE_CATALOG"] = CATALOG

import agentlib  # noqa: E402
import bridgeprotocol  # noqa: E402
import chatlib  # noqa: E402
import eventbus  # noqa: E402
import llmlib  # noqa: E402
import settingslib  # noqa: E402
import ticketlib  # noqa: E402
import usagelib  # noqa: E402


def fail(message):
    print(f"chattest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def wait_for(predicate, timeout=5.0, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    fail(f"timed out waiting for {what}")


# ------------------------------------------------------------- mock API
HITS = []


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        HITS.append({"path": self.path, "model": body.get("model"),
                     "stream": body.get("stream")})
        if self.path.startswith("/v1/messages"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "id": "msg-mock", "model": "claude-sonnet-4-6",
                "content": [{"type": "text", "text": "claude-hi"}],
                "usage": {"input_tokens": 100,
                          "output_tokens": 50}}).encode("utf-8"))
            return
        if body.get("model") == "mock-fail":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"error": {"message": "mock boom"}}).encode("utf-8"))
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        no_usage = "nousage" in self.path
        chunks = [
            {"id": "s1", "model": body.get("model"),
             "choices": [{"index": 0, "delta": {
                 "role": "assistant", "content": "Hel"},
                 "finish_reason": None}]},
            {"id": "s1", "model": body.get("model"),
             "choices": [{"index": 0, "delta": {"content": "lo "},
                          "finish_reason": None}]},
            {"id": "s1", "model": body.get("model"),
             "choices": [{"index": 0, "delta": {"content": "world"},
                          "finish_reason": None}]},
        ]
        last = {"id": "s1", "model": body.get("model"),
                "choices": [{"index": 0, "delta": {},
                             "finish_reason": "stop"}]}
        if not no_usage:
            last["usage"] = {"prompt_tokens": 1000,
                             "completion_tokens": 500}
        chunks.append(last)
        for index, chunk in enumerate(chunks):
            self.wfile.write(
                ("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))
            self.wfile.flush()
            if index == 0:
                time.sleep(1.0)  # prove deltas precede completion
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, *_args):
        pass


SERVER = HTTPServer(("127.0.0.1", 0), MockHandler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
PORT = SERVER.server_address[1]
SSE_URL = f"http://127.0.0.1:{PORT}/sse/chat/completions"
SSE_NOUSAGE_URL = f"http://127.0.0.1:{PORT}/nousage/chat/completions"
CLAUDE_URL = f"http://127.0.0.1:{PORT}/v1/messages"


def run_cli_stream(request, keys):
    """Run the real dclawos-llm --stream; return (lines, times)."""
    if LLM_BIN is None:
        fail("dclawos-llm binary not found")
    env = dict(os.environ)
    env["DCLAWOS_LLM_KEYS"] = json.dumps(keys)
    proc = subprocess.Popen(
        [LLM_BIN, "--stream"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, text=True, env=env)
    proc.stdin.write(json.dumps(request))
    proc.stdin.close()
    lines, times = [], []
    started = time.time()
    for line in proc.stdout:
        lines.append(json.loads(line))
        times.append(time.time() - started)
    proc.wait(timeout=30)
    if proc.returncode != 0:
        fail(f"dclawos-llm --stream exited {proc.returncode}")
    return lines, times


def load_daemon_module():
    candidates = [
        "/usr/local/bin/dclawos-agent",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "bin", "dclawos-agent"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            import types as _types
            module = _types.ModuleType("dclawos_agent_daemon_chat")
            module.__file__ = path
            with open(path, "r", encoding="utf-8") as handle:
                code = compile(handle.read(), path, "exec")
            exec(code, module.__dict__)  # noqa: S102 - our own daemon
            return module
    fail("daemon binary not found")


class Collector:
    def __init__(self):
        self.events = []
        self.lock = threading.Lock()

    def __call__(self, event):
        with self.lock:
            self.events.append(event)

    def llm_deltas(self):
        with self.lock:
            return [e for e in self.events
                    if e.get("type") == "llm"
                    and isinstance(e.get("data"), dict)
                    and "delta" in e["data"]]


def main():
    if not os.path.isfile("/usr/share/dclawos/engine/llm.js"):
        fail("engine bundle missing (hook 1003 must build llm.js)")

    # (b2) No keys: llmlib sends nothing, returns an honest null.
    hits_before = len(HITS)
    result = llmlib.call_stream(
        [{"role": "user", "content": "hi"}],
        provider="openai", model="gpt-4o-mini")
    if result.get("response") is not None:
        fail("no-key call fabricated a response")
    # (the decision path with zero keys produces no targets; and
    # even the engine must not have been asked to hit the mock)
    if len(HITS) != hits_before:
        fail("no-key call sent a request to the mock")

    # Dummy key for the remaining sections.
    with open(os.path.join(os.environ["DCLAWOS_SECRETS_DIR"],
                           "llm-openai"), "w",
              encoding="utf-8") as handle:
        handle.write("test-key-openai")
    with open(os.path.join(os.environ["DCLAWOS_SECRETS_DIR"],
                           "llm-anthropic"), "w",
              encoding="utf-8") as handle:
        handle.write("test-key-anthropic")

    # (a) True SSE streaming through the real binary.
    request = {
        "messages": [{"role": "user", "content": "hello"}],
        "targets": [{"provider": "openai", "model": "gpt-4o-mini"}],
        "baseUrlOverrides": {"openai": SSE_URL},
    }
    lines, times = run_cli_stream(request, {"openai": "test-key"})
    deltas = [ln for ln in lines if ln.get("type") == "delta"]
    results = [ln for ln in lines if ln.get("type") == "result"]
    if len(results) != 1:
        fail(f"expected exactly one result line, got {len(results)}")
    if not deltas:
        fail("stream produced no delta lines")
    if times[0] > times[-1] - 0.5:
        fail("first delta did not arrive well before completion "
             f"(first {times[0]:.2f}s, last {times[-1]:.2f}s)")
    assembled = "".join(d.get("text", "") for d in deltas)
    if assembled != "Hello world":
        fail(f"delta assembly wrong: {assembled!r}")
    result = results[0]
    content = result["response"]["choices"][0]["message"]["content"]
    if content != assembled:
        fail("result content differs from assembled deltas")
    if result.get("stream_mode") != "sse":
        fail(f"SSE result stream_mode is {result.get('stream_mode')!r}")
    if any(d.get("stream_mode") != "sse" for d in deltas):
        fail("a delta line was not labelled sse")
    envelope = result.get("cost_envelope") or {}
    usage = envelope.get("usage") or {}
    if usage.get("promptTokens") != 1000 or \
            usage.get("completionTokens") != 500:
        fail(f"usage not carried from the stream: {usage!r}")
    if result.get("costUsd") is None or \
            abs(result["costUsd"] - 0.00045) > 1e-9 or \
            not envelope.get("priced"):
        fail(f"cost not priced from mock usage: {result.get('costUsd')}")

    # No-usage stream: cost stays null, never invented.
    request["baseUrlOverrides"] = {"openai": SSE_NOUSAGE_URL}
    lines, _times = run_cli_stream(request, {"openai": "test-key"})
    result = [ln for ln in lines if ln.get("type") == "result"][0]
    if result.get("stream_mode") != "sse":
        fail("no-usage stream lost stream_mode=sse")
    if result.get("costUsd") is not None or \
            result.get("cost_envelope") is not None:
        fail("no-usage stream invented a cost")

    # (b1) Failure BEFORE the first delta: honest failure, one
    # attempt for the single target, no fabricated response.
    fail_request = {
        "messages": [{"role": "user", "content": "hello"}],
        "targets": [{"provider": "openai", "model": "mock-fail"}],
        "baseUrlOverrides": {"openai": SSE_URL},
    }
    lines, _times = run_cli_stream(fail_request, {"openai": "test-key"})
    result = [ln for ln in lines if ln.get("type") == "result"][0]
    if result.get("response") is not None:
        fail("pre-delta 500 fabricated a response")
    if len(result.get("attempts") or []) != 1 or \
            result["attempts"][0].get("status") != 500:
        fail(f"failure attempts wrong: {result.get('attempts')!r}")
    if [ln for ln in lines if ln.get("type") == "delta"]:
        fail("failed stream emitted deltas")

    # (d) Claude format: staged, never labelled sse.
    claude_request = {
        "messages": [{"role": "user", "content": "hello"}],
        "targets": [{"provider": "anthropic",
                     "model": "claude-sonnet-4-6"}],
        "baseUrlOverrides": {"anthropic": CLAUDE_URL},
    }
    lines, _times = run_cli_stream(
        claude_request, {"anthropic": "test-key"})
    result = [ln for ln in lines if ln.get("type") == "result"][0]
    if result.get("stream_mode") != "staged":
        fail(f"claude path labelled {result.get('stream_mode')!r}, "
             "expected 'staged'")
    staged = [ln for ln in lines if ln.get("type") == "delta"]
    if not staged or any(d.get("stream_mode") != "staged"
                         for d in staged):
        fail("claude staged delta missing or mislabelled")
    if result["response"]["choices"][0]["message"]["content"] \
            != "claude-hi":
        fail("claude staged content wrong")

    # (c) Daemon run_llm_task: bus subscribers receive the llm
    # delta events in order; usage ledger gains exactly one record
    # (no-usage endpoint -> cost stays null).
    os.environ["DCLAWOS_LLM_BASE_URL_OPENAI"] = SSE_NOUSAGE_URL
    server = eventbus.EventBusServer().start()
    collector = Collector()
    subscriber = eventbus.EventSubscriber(collector).start()
    try:
        time.sleep(0.3)
        daemon = load_daemon_module()
        records_before, _bad = usagelib.read_usage()
        task = agentlib.new_task("stream chat", "say hi", [])
        task["kind"] = "llm"
        task["conv_session"] = "aaaaaaaaaaaaaaaa"
        agentlib.save_task(task)
        done = daemon.run_llm_task(task)
        if done.get("status") != "done":
            fail(f"daemon llm task not done: {done.get('status')} "
                 f"{done.get('error')}")
        if (done.get("result") or {}).get("reply") != "Hello world":
            fail(f"daemon reply wrong: {done.get('result')!r}")
        if (done.get("result") or {}).get("stream_mode") != "sse":
            fail("daemon result lost stream_mode=sse")
        wait_for(lambda: len(collector.llm_deltas()) >= 1,
                 what="llm delta events on the bus")
        time.sleep(0.2)
        events = collector.llm_deltas()
        seqs = [(e.get("data") or {}).get("seq") for e in events]
        if seqs != sorted(seqs):
            fail(f"llm delta seqs out of order: {seqs!r}")
        joined = "".join((e.get("data") or {}).get("delta", "")
                         for e in events)
        if joined != "Hello world":
            fail(f"bus delta assembly wrong: {joined!r}")
        if any((e.get("data") or {}).get("stream_mode") != "sse"
               for e in events):
            fail("a bus llm event was not labelled sse")
        if any(e.get("task_id") != task["id"] for e in events):
            fail("bus llm events lack the task_id")
        records_after, _bad = usagelib.read_usage()
        if len(records_after) != len(records_before) + 1:
            fail("streaming call did not append exactly one usage "
                 f"record ({len(records_before)} -> "
                 f"{len(records_after)})")
        if records_after[-1].get("cost_usd") is not None:
            fail("no-usage stream recorded a non-null cost")
    finally:
        subscriber.stop()
        server.stop()
        os.environ.pop("DCLAWOS_LLM_BASE_URL_OPENAI", None)

    # (e) Approval: host verdict writes match dclawos-approve, and
    # bridge payloads never carry a secret value in clear text.
    host = chatlib.ChatHost(conv_session="bbbbbbbbbbbbbbbb")
    ticket = ticketlib.new_ticket(
        "shell",
        {"argv": ["echo", "hi"], "api_key": "supersecret-value",
         "path": "/tmp/x"},
        "shell echo hi", 600)
    ticketlib.ensure_dir(ticketlib.tickets_dir())
    ticketlib.save_ticket(ticket)
    host.note_approval_event({
        "type": "approval", "summary": "approval requested",
        "data": {"ticket_id": ticket["id"], "tool": "shell",
                 "tier": 1, "summary": "shell echo hi",
                 "params": bridgeprotocol.redact_params(
                     {"argv": ["echo", "hi"],
                      "api_key": "supersecret-value",
                      "path": "/tmp/x"})}})
    state = host.dispatch("chat.state", {})
    blob = json.dumps(state, ensure_ascii=False)
    if "supersecret-value" in blob:
        fail("chat.state payload leaks a secret param value")
    if "<redacted>" not in blob:
        fail("chat.state payload lacks the redacted marker")
    out = host.dispatch("chat.approve", {"ticket_id": ticket["id"]})
    if out.get("status") != "approved":
        fail(f"chat.approve result wrong: {out!r}")
    saved = ticketlib.load_ticket(ticket["id"])
    if saved.get("status") != "approved" or \
            saved.get("approved_by") != "human-gui" or \
            not isinstance(saved.get("approved_at"), int):
        fail(f"approved ticket fields diverge from dclawos-approve: "
             f"{saved!r}")
    ticket2 = ticketlib.new_ticket("shell", {"argv": ["ls"]},
                                   "shell ls", 600)
    ticketlib.save_ticket(ticket2)
    host.dispatch("chat.deny", {"ticket_id": ticket2["id"]})
    saved2 = ticketlib.load_ticket(ticket2["id"])
    if saved2.get("status") != "denied" or \
            saved2.get("approved_by") != "human-gui":
        fail(f"denied ticket fields wrong: {saved2!r}")

    # agentlib's own approval event is redacted before publishing.
    settingslib.apply_setting("agent-full-access", True)
    verdict, tid = agentlib.request_approval(
        "shell", {"argv": ["x"], "api_key": "supersecret-two"},
        "shell x")
    if verdict != "approved":
        fail("full-access approval did not auto-approve")
    with open(os.environ["DCLAWOS_EVENTS_PATH"],
              encoding="utf-8") as handle:
        event_lines = handle.read()
    if "supersecret-two" in event_lines:
        fail("approval event in events.jsonl leaks a secret value")
    approval_lines = [json.loads(ln) for ln in
                      event_lines.splitlines()
                      if '"approval"' in ln]
    if not approval_lines or \
            (approval_lines[-1].get("data") or {}).get(
                "ticket_id") != tid:
        fail("approval event lacks the ticket_id data")
    settingslib.apply_setting("agent-full-access", False)

    # (f) parse_command transplant fidelity.
    case = chatlib.parse_command("/run ls -la")
    if case.get("steps") != [{"tool": "shell",
                              "params": {"argv": ["ls", "-la"]}}]:
        fail(f"/run steps changed: {case!r}")
    case = chatlib.parse_command("/write /tmp/a.txt hello world")
    if case.get("steps") != [{"tool": "write_file", "params": {
            "path": "/tmp/a.txt", "content": "hello world"}}]:
        fail(f"/write steps changed: {case!r}")
    case = chatlib.parse_command('/mcp srv tool {"a": 1}')
    if case.get("steps") != [{"tool": "mcp_call", "params": {
            "server": "srv", "tool": "tool",
            "arguments": {"a": 1}}}]:
        fail(f"/mcp steps changed: {case!r}")
    case = chatlib.parse_command("/subagent do the thing")
    if case.get("steps") != [{"tool": "subagent", "params": {
            "goal": "do the thing", "steps": []}}]:
        fail(f"/subagent steps changed: {case!r}")

    # Bridge allowlist: M3 verbs live on chat only.
    for verb in ("chat.state", "chat.assign", "chat.switch",
                 "chat.new", "chat.model.set", "chat.approve",
                 "chat.deny", "chat.stop", "chat.copy", "chat.launch"):
        bridgeprotocol.validate_command(
            {"kind": "command", "protocol": 1, "id": "x1",
             "command": verb, "params": {}}, "chat")
        try:
            bridgeprotocol.validate_command(
                {"kind": "command", "protocol": 1, "id": "x2",
                 "command": verb, "params": {}}, "settings")
            fail(f"{verb} was allowlisted on the settings surface")
        except bridgeprotocol.BridgeError:
            pass

    # Host send/model/session verbs end-to-end (task creation only;
    # execution is the daemon's job, gated in section (c)).
    sent = host.dispatch("chat.send", {"text": "hello there"})
    if sent.get("action") != "task":
        fail(f"chat.send plain text wrong: {sent!r}")
    created = agentlib.load_task(sent["task_id"])
    if created.get("kind") != "llm" or \
            created.get("conv_session") != "bbbbbbbbbbbbbbbb":
        fail("chat.send did not create an llm task bound to the "
             f"conversation: {created!r}")
    sent = host.dispatch("chat.send", {"text": "/run echo hi"})
    created = agentlib.load_task(sent["task_id"])
    if (created.get("steps") or [{}])[0].get("tool") != "shell":
        fail("chat.send /run did not create a shell step task")
    host.dispatch("chat.model.set",
                  {"provider": "openai", "model": "gpt-4o-mini"})
    if settingslib.get_setting("ai-provider") != "openai" or \
            settingslib.get_setting("ai-model") != "gpt-4o-mini":
        fail("chat.model.set did not write the shared settings")
    model = host.dispatch("chat.state", {}).get("model") or {}
    if model.get("provider") != "openai" or \
            not model.get("configured"):
        fail(f"chat.state model view wrong: {model!r}")
    new = host.dispatch("chat.new", {})
    if new.get("session") == "bbbbbbbbbbbbbbbb" or \
            len(new.get("session", "")) != 16:
        fail(f"chat.new session wrong: {new!r}")

    print("chattest: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

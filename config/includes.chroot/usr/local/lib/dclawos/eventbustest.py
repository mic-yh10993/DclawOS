#!/usr/bin/env python3
"""Event-bus + bridge-protocol + live-output gate (Phase 6 M1).

Headless; only touches a temp dir. Verifies:
1. The bus server starts on a temp socket; a subscriber receives a
   published event; agentlib.log_event still writes the durable
   events.jsonl line (and its publish reaches the subscriber).
2. A malformed line and a client disconnect do not kill the server
   (a later publish is still delivered).
3. The bridge protocol core: valid hello/command accepted,
   malformed envelopes refused, a non-allowlisted verb refused,
   secret params refused on non-secret commands, replies/events
   strip secrets.
4. LIVE shell output: the real daemon _run_shell streams stdout
   and stderr chunks as bus events BEFORE the process exits (a
   print -> sleep -> print command; the first chunk must arrive
   well before the run returns, and chunk order is preserved),
   and the result contract (returncode, stdout, stderr) is intact.
5. Task wake: agentlib.save_task publishes a bus task signal, and
   the inotify DirectoryWatcher reports a new task file promptly.

Exits non-zero on the first failed assertion. Invoked by build
hook 1008 inside the image chroot; also runnable by hand.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, "/usr/local/lib/dclawos")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = tempfile.mkdtemp(prefix="dclawos-eventbustest-")
os.environ["DCLAWOS_TASKS_DIR"] = os.path.join(ROOT, "tasks")
os.environ["DCLAWOS_EVENTS_PATH"] = os.path.join(ROOT, "events.jsonl")
os.environ["DCLAWOS_EVENTBUS_SOCKET"] = os.path.join(ROOT, "bus.sock")
os.environ["DCLAWOS_AGENT_WORKSPACE"] = os.path.join(ROOT, "workspace")
os.makedirs(os.environ["DCLAWOS_AGENT_WORKSPACE"], exist_ok=True)

import agentlib  # noqa: E402
import bridgeprotocol  # noqa: E402
import eventbus  # noqa: E402


def fail(message):
    print(f"eventbustest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def wait_for(predicate, timeout=5.0, what="condition"):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    fail(f"timed out waiting for {what}")


class Collector:
    def __init__(self):
        self.events = []
        self.times = []
        self.lock = threading.Lock()

    def __call__(self, event):
        with self.lock:
            self.events.append(event)
            self.times.append(time.time())

    def summaries(self):
        with self.lock:
            return [e.get("summary", "") for e in self.events]


def load_daemon_module():
    candidates = [
        "/usr/local/bin/dclawos-agent",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "..", "bin", "dclawos-agent"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            import types as _types
            module = _types.ModuleType("dclawos_agent_daemon")
            module.__file__ = path
            with open(path, "r", encoding="utf-8") as handle:
                code = compile(handle.read(), path, "exec")
            exec(code, module.__dict__)  # noqa: S102 - our own daemon
            return module
    fail("daemon binary not found for _run_shell import")


def main():
    received_wake = []
    server = eventbus.EventBusServer(
        on_event=lambda e: received_wake.append(e)).start()
    collector = Collector()
    subscriber = eventbus.EventSubscriber(collector).start()
    try:
        # 1. publish -> subscriber receives; durable log written.
        agentlib.log_event("system", "bus-hello", session="t1")
        wait_for(lambda: "bus-hello" in collector.summaries(),
                 what="subscriber to receive log_event publish")
        with open(os.environ["DCLAWOS_EVENTS_PATH"],
                  encoding="utf-8") as handle:
            lines = [json.loads(ln) for ln in handle if ln.strip()]
        if not any(e.get("summary") == "bus-hello" for e in lines):
            fail("durable events.jsonl lacks the bus-hello line")
        if not any(e.get("id") for e in lines):
            fail("event envelope lacks the M1 id field")

        # 2. malformed line + disconnect do not kill the server.
        raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        raw.settimeout(5)
        raw.connect(os.environ["DCLAWOS_EVENTBUS_SOCKET"])
        raw.sendall(b"this is not json\n{\"kind\":\"bogus\"}\n")
        raw.sendall(b"\n")
        time.sleep(0.2)
        raw.close()  # abrupt disconnect
        if not eventbus.publish_event(
                {"type": "system", "summary": "after-malformed",
                 "session": ""}):
            fail("publish after malformed line was not acked")
        wait_for(lambda: "after-malformed" in collector.summaries(),
                 what="delivery after malformed line")

        # 3. bridge protocol core.
        if bridgeprotocol.validate_hello(
                {"kind": "hello", "protocol": 1,
                 "surface": "chat"}) != 1:
            fail("valid bridge hello refused")
        good = bridgeprotocol.validate_command(
            {"kind": "command", "protocol": 1, "id": "r1",
             "command": "chat.send", "params": {"text": "hi"}},
            "chat")
        if good["command"] != "chat.send":
            fail("valid bridge command refused")
        for bad, surface, why in (
                ({"kind": "command", "id": "r2",
                  "command": "chat.send"}, "dashboard",
                 "cross-surface verb"),
                ({"kind": "command", "id": "r3",
                  "command": "os.system"}, "chat",
                 "non-allowlisted verb"),
                ({"kind": "command", "id": "r4",
                  "command": "chat.send",
                  "params": {"token": "x"}}, "chat",
                 "secret param on non-secret command"),
                ({"kind": "command", "command": "chat.send",
                  "params": {}}, "chat", "missing id")):
            try:
                bridgeprotocol.validate_command(bad, surface)
                fail(f"bridge accepted {why}")
            except bridgeprotocol.BridgeError:
                pass
        reply = bridgeprotocol.make_reply(
            "r1", {"token": "should-not-echo", "ok": 1})
        if "token" in json.dumps(reply):
            fail("bridge reply echoes a secret key")
        secret_cmd = bridgeprotocol.validate_command(
            {"kind": "command", "id": "r5", "command": "secret.set",
             "params": {"name": "k", "token": "v"}}, "settings")
        redacted = bridgeprotocol.redact_params(secret_cmd["params"])
        if redacted.get("token") != "<redacted>":
            fail("bridge redact_params does not redact secrets")

        # 4. live shell streaming via the real daemon code.
        daemon = load_daemon_module()
        collector2 = Collector()
        sub2 = eventbus.EventSubscriber(collector2).start()
        try:
            time.sleep(0.3)  # let sub2 subscribe
            script = (
                "import sys,time;"
                "print('chunk-one', flush=True);"
                "time.sleep(1.2);"
                "print('chunk-two', file=sys.stderr, flush=True)")
            started = time.time()
            result_box = {}

            def _run():
                result_box["result"] = daemon._run_shell(
                    {"argv": [sys.executable, "-c", script]},
                    context={"session": "live", "task_id": "t-live",
                             "step": 0})
                result_box["done"] = time.time()

            runner = threading.Thread(target=_run)
            runner.start()
            wait_for(
                lambda: any(
                    (e.get("data") or {}).get("stream") == "stdout"
                    and "chunk-one" in
                    ((e.get("data") or {}).get("chunk") or "")
                    for e in collector2.events),
                what="first live stdout chunk")
            with collector2.lock:
                first_idx = next(
                    i for i, e in enumerate(collector2.events)
                    if "chunk-one" in
                    ((e.get("data") or {}).get("chunk") or ""))
                first_at = collector2.times[first_idx]
            runner.join(timeout=10)
            if runner.is_alive():
                fail("_run_shell did not return")
            result = result_box["result"]
            if result_box["done"] - first_at < 0.5:
                fail("first chunk did not arrive well before exit "
                     "(output is not live)")
            if result["returncode"] != 0 \
                    or "chunk-one" not in result["stdout"] \
                    or "chunk-two" not in result["stderr"]:
                fail(f"shell result contract broken: {result!r}")
            chunks = [((e.get("data") or {}).get("chunk") or "")
                      for e in collector2.events
                      if e.get("type") == "output"]
            if not any("chunk-two" in c for c in chunks):
                fail("stderr chunk was not streamed as an event")
            phases = [((e.get("data") or {}).get("phase"))
                      for e in collector2.events
                      if e.get("type") == "command"]
            if "start" not in phases or "finish" not in phases:
                fail("shell start/finish events missing")
        finally:
            sub2.stop()

        # 5. task wake: save_task publishes a bus task signal, and
        # inotify reports the new task file.
        watcher = eventbus.DirectoryWatcher(
            os.environ["DCLAWOS_TASKS_DIR"]) \
            if os.path.isdir(os.environ["DCLAWOS_TASKS_DIR"]) else None
        if watcher is None:
            agentlib.ensure_exchange_dir(os.environ["DCLAWOS_TASKS_DIR"])
            watcher = eventbus.DirectoryWatcher(
                os.environ["DCLAWOS_TASKS_DIR"])
        import select as _select
        task = agentlib.new_task("wake-test", "goal", [])
        agentlib.save_task(task)
        wait_for(lambda: any(
            e.get("type") == "task"
            and (e.get("data") or {}).get("task_id") == task["id"]
            for e in received_wake),
            what="bus task wake signal from save_task")
        ready, _w, _x = _select.select([watcher.fileno()], [], [], 2.0)
        if not ready or not watcher.drain():
            fail("inotify watcher did not report the new task file")
        watcher.close()

        # shell error semantics preserved.
        try:
            daemon._run_shell({"argv": ["/nonexistent-cmd-xyz"]})
            fail("missing command did not raise")
        except ValueError as exc:
            if "command not found" not in str(exc):
                fail(f"missing-command message changed: {exc}")
        try:
            daemon._run_shell({"argv": [sys.executable, "-c",
                                        "import time; time.sleep(5)"],
                               "timeout": 1})
            fail("slow command did not time out")
        except ValueError as exc:
            if "timed out" not in str(exc):
                fail(f"timeout message changed: {exc}")
    finally:
        subscriber.stop()
        server.stop()
    print("eventbustest: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Agent-loop end-to-end selftest (Phase 4 module 2, build gate).

Runs the REAL agent daemon (--once) against a local mock
OpenAI-compatible endpoint: no real API keys (a dummy key in a temp
secrets dir), no external network. Verifies, end to end:

1. An llm task whose mock model first requests a tool call
   (list_files, tier 0) and then answers: status done, reply is the
   mock's final text, cost_usd equals the sum over both turns of
   usage x registry pricing, and the conversation transcript holds
   user -> assistant(tool_calls) -> tool -> assistant entries.
2. A second llm task with an EMPTY secrets dir: done with
   model_configured False and no reply (honest model-less path).

Exits non-zero on the first failed assertion. Invoked by build hook
1008 inside the image chroot; also runnable by hand on a live system
(it only touches a temp dir).
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/usr/local/lib/dclawos")

ROOT = tempfile.mkdtemp(prefix="dclawos-looptest-")
os.environ["DCLAWOS_TASKS_DIR"] = os.path.join(ROOT, "tasks")
os.environ["DCLAWOS_EVENTS_PATH"] = os.path.join(ROOT, "events.jsonl")
os.environ["DCLAWOS_SECRETS_DIR"] = os.path.join(ROOT, "secrets")
os.environ["DCLAWOS_CONV_DIR"] = os.path.join(ROOT, "conversations")
os.environ["DCLAWOS_STOP_FLAG"] = os.path.join(ROOT, "stop.json")
os.environ["DCLAWOS_AGENT_WORKSPACE"] = os.path.join(ROOT, "workspace")
os.makedirs(os.environ["DCLAWOS_AGENT_WORKSPACE"], exist_ok=True)
with open(os.path.join(os.environ["DCLAWOS_AGENT_WORKSPACE"],
                       "hello.txt"), "w", encoding="utf-8") as handle:
    handle.write("hi\n")

import agentlib  # noqa: E402
import convlib  # noqa: E402


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages") or []
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        if not has_tool_result:
            payload = {
                "id": "mock-turn-1",
                "model": body.get("model", "gpt-4o-mini"),
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "list_files",
                                "arguments": json.dumps(
                                    {"path": os.environ[
                                        "DCLAWOS_AGENT_WORKSPACE"]}),
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        else:
            payload = {
                "id": "mock-turn-2",
                "model": body.get("model", "gpt-4o-mini"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant",
                                "content": "mock-final"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 200, "completion_tokens": 100},
            }
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def fail(message):
    print(f"agentlooptest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def run_agent_once():
    proc = subprocess.run(
        ["/usr/local/bin/dclawos-agent", "--once"],
        capture_output=True, text=True, timeout=180,
        env=dict(os.environ))
    if proc.returncode != 0:
        fail(f"agent --once exited {proc.returncode}: "
             f"{proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def main():
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    os.environ["DCLAWOS_LLM_BASE_URL_OPENAI"] = \
        f"http://127.0.0.1:{port}/v1/chat/completions"

    # Dummy key for provider "openai" (never a real credential).
    os.makedirs(agentlib.secrets_dir(), exist_ok=True)
    secret_path = os.path.join(agentlib.secrets_dir(), "llm-openai")
    with open(secret_path, "w", encoding="utf-8") as handle:
        handle.write("mock-key\n")
    os.chmod(secret_path, 0o600)

    task = agentlib.new_task("loop test", "list my files", [])
    task["kind"] = "llm"
    task["conv_session"] = task["session"]
    # Pin provider+model: in auto mode the router legitimately picks
    # the provider's first catalog model, whose pricing differs; the
    # cost assertion below is for gpt-4o-mini's registry pricing.
    task["provider"] = "openai"
    task["model"] = "gpt-4o-mini"
    agentlib.save_task(task)
    outcome = run_agent_once()
    if not outcome.get("ran"):
        fail("agent did not run the queued task")
    done = agentlib.load_task(task["id"])
    if done.get("status") != "done":
        fail(f"llm task status {done.get('status')}: "
             f"{done.get('error')}")
    result = done.get("result") or {}
    if result.get("reply") != "mock-final":
        fail(f"reply is not the mock final text: {result}")
    # gpt-4o-mini pricing 0.15 in / 0.6 out per 1M:
    # turn1 100/50 -> 0.000045, turn2 200/100 -> 0.00009.
    expected = 0.000045 + 0.00009
    if result.get("cost_usd") is None or \
            abs(result["cost_usd"] - expected) > 1e-9:
        fail(f"cost_usd {result.get('cost_usd')} != {expected}")
    entries = convlib.load_entries(task["session"])
    kinds = [(e.get("kind"), (e.get("message") or {}).get("role"))
             for e in entries]
    roles = [role for kind, role in kinds if kind == "message"]
    if roles != ["user", "assistant", "tool", "assistant"]:
        fail(f"transcript roles wrong: {roles}")

    # Honest model-less path: empty the secrets dir, run again.
    os.remove(secret_path)
    task2 = agentlib.new_task("no model", "hello?", [])
    task2["kind"] = "llm"
    task2["conv_session"] = task2["session"]
    agentlib.save_task(task2)
    run_agent_once()
    done2 = agentlib.load_task(task2["id"])
    result2 = done2.get("result") or {}
    if done2.get("status") != "done" or \
            result2.get("model_configured") is not False or \
            result2.get("reply"):
        fail(f"model-less path not honest: {done2.get('status')} "
             f"{result2}")
    # --- Phase 4 module 6 gate: cron scheduling ----------------
    import cronlib
    cron_tmp = tempfile.mkdtemp(prefix="dclawos-cron-test-")
    os.environ["DCLAWOS_CRON_JOBS"] = os.path.join(cron_tmp,
                                                   "jobs.json")
    import time as _time
    fri = _time.strptime("2026-10-30 09:30", "%Y-%m-%d %H:%M")
    if not cronlib.cron_matches("30 9 * * 5", fri):
        fail("cron matcher: weekday match failed")
    if cronlib.cron_matches("30 9 * * 1", fri):
        fail("cron matcher: weekday mismatch accepted")
    job = cronlib.add_job("every 30", "gate check")
    before = len(agentlib.list_tasks())
    created = cronlib.fire_due(int(_time.time()) + 60)
    if len(created) != 1 or len(agentlib.list_tasks()) != before + 1:
        fail(f"cron fire_due created {created}, expected one task")
    if cronlib.fire_due(int(_time.time()) + 60):
        fail("cron job fired twice for one interval")
    if not cronlib.remove_job(job["id"]):
        fail("cron remove_job failed")
    server.shutdown()
    print("agentlooptest OK: tool-call round trip, transcript, cost "
          "accounting and honest model-less path verified against "
          "the local mock endpoint")


if __name__ == "__main__":
    main()

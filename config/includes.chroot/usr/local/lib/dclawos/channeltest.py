#!/usr/bin/env python3
"""Channel runtime selftest (Phase 5 module 1, build gate).

Runs channellib + the Telegram adapter + the agent daemon's
channel poll/flush functions against a LOCAL MOCK Bot API server:
no real token (a dummy secret file), no external network.
Verifies, end to end:

1. Allowlist filtering: a poll containing one allowlisted and one
   stranger update yields exactly the allowlisted event; the
   stranger is counted as dropped in the channel state, and the
   adapter cursor advances past BOTH updates (no redelivery).
2. Daemon wiring: _poll_channels turns the next allowlisted
   update into exactly one llm task and records a pending reply
   addressed to the originating chat; a second poll creates
   nothing new.
3. Reply routing: when the task finishes with a reply,
   _flush_pending_replies delivers it via sendMessage to the
   ORIGINATING chat id, and the pending map drains.

Phase 5 modules 2-3 extend the same local-mock pattern to the
Discord adapter (mock REST), the Slack adapter (mock HTTP API +
a hand-rolled RFC6455 websocket server standing in for Socket
Mode) and the Matrix adapter (mock client-server sync: baseline
cursor, encrypted-event skip, reply PUT).

Exits non-zero on the first failed assertion. Invoked by build
hook 1008 inside the image chroot; also runnable by hand (it only
touches a temp dir).
"""

import base64
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "/usr/local/lib/dclawos")

ROOT = tempfile.mkdtemp(prefix="dclawos-chantest-")
os.environ["DCLAWOS_TASKS_DIR"] = os.path.join(ROOT, "tasks")
os.environ["DCLAWOS_EVENTS_PATH"] = os.path.join(ROOT, "events.jsonl")
os.environ["DCLAWOS_SECRETS_DIR"] = os.path.join(ROOT, "secrets")
os.environ["DCLAWOS_CONV_DIR"] = os.path.join(ROOT, "conversations")
os.environ["DCLAWOS_STOP_FLAG"] = os.path.join(ROOT, "stop.json")
os.environ["DCLAWOS_AGENT_WORKSPACE"] = os.path.join(ROOT, "workspace")
os.environ["DCLAWOS_CHANNELS_STATE"] = os.path.join(ROOT, "channels")
os.environ["DCLAWOS_CONNECTOR_PENDING"] = os.path.join(
    ROOT, "pending.json")
os.makedirs(os.environ["DCLAWOS_AGENT_WORKSPACE"], exist_ok=True)

import agentlib  # noqa: E402
import channellib  # noqa: E402

UPDATES = [
    {"update_id": 10, "message": {
        "text": "hello channel",
        "from": {"id": 111}, "chat": {"id": 555}}},
    {"update_id": 11, "message": {
        "text": "stranger speaks",
        "from": {"id": 999}, "chat": {"id": 777}}},
]
SENT = []


class MockTelegram(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _reply(self, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/getUpdates"):
            offset = int(body.get("offset") or 0)
            result = [u for u in UPDATES
                      if u["update_id"] >= offset]
            self._reply({"ok": True, "result": result})
            return
        if self.path.endswith("/sendMessage"):
            SENT.append(body)
            self._reply({"ok": True,
                         "result": {"message_id": 42}})
            return
        self._reply({"ok": False, "description": "unknown method"})


def fail(message):
    print(f"channeltest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


# ------------------------------------------------- modules 2-3 --
# Discord / Slack / Matrix mock fixtures. Same honesty rules:
# loopback only, dummy tokens, no real service is contacted.

def _register_channel(entry, secrets):
    data = {"skills": [], "mcp_servers": [], "connectors": [],
            "channels": [entry]}
    with open(os.environ["DCLAWOS_REGISTRY"], "w",
              encoding="utf-8") as handle:
        json.dump(data, handle)
    for name, text in secrets.items():
        path = os.path.join(agentlib.secrets_dir(), name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        os.chmod(path, 0o600)


class _JsonHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, payload, status=200):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")


DISCORD_MESSAGES = [
    {"id": "1003", "content": "stranger here",
     "author": {"id": "999"}, "channel_id": "900"},
    {"id": "1002", "content": "second from owner",
     "author": {"id": "42"}, "channel_id": "900"},
    {"id": "1001", "content": "first from owner",
     "author": {"id": "42"}, "channel_id": "900"},
]
DISCORD_SENT = []


class MockDiscord(_JsonHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.endswith("/users/@me"):
            self._reply({"id": "777", "username": "clawbot"})
            return
        if parsed.path.endswith("/channels/900/messages"):
            after = int((parse_qs(parsed.query).get("after")
                         or ["0"])[0])
            self._reply([m for m in DISCORD_MESSAGES
                         if int(m["id"]) > after])
            return
        self._reply({"message": "not found"}, status=404)

    def do_POST(self):
        if urlparse(self.path).path.endswith(
                "/channels/900/messages"):
            DISCORD_SENT.append(self._body())
            self._reply({"id": "2001"})
            return
        self._reply({"message": "not found"}, status=404)


MATRIX_PUTS = []


class MockMatrix(_JsonHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path.endswith("/account/whoami"):
            self._reply({"user_id": "@bot:test"})
            return
        if parsed.path.endswith("/sync"):
            query = parse_qs(parsed.query)
            owner_msg = {
                "type": "m.room.message", "sender": "@owner:test",
                "event_id": "$e1",
                "content": {"msgtype": "m.text",
                            "body": "hi matrix"}}
            if "since" not in query:
                self._reply({
                    "next_batch": "s1",
                    "rooms": {"join": {"!r:test": {"timeline": {
                        "events": [dict(
                            owner_msg, event_id="$old")]}}}}})
                return
            if query["since"][0] == "s1":
                encrypted = {
                    "type": "m.room.encrypted",
                    "sender": "@owner:test", "event_id": "$e2",
                    "content": {"ciphertext": "opaque"}}
                own_msg = dict(owner_msg, event_id="$e3",
                               sender="@bot:test")
                self._reply({
                    "next_batch": "s2",
                    "rooms": {"join": {"!r:test": {"timeline": {
                        "events": [encrypted, owner_msg,
                                   own_msg]}}}}})
                return
            self._reply({"next_batch": "s3",
                         "rooms": {"join": {}}})
            return
        self._reply({"errcode": "M_NOT_FOUND"}, status=404)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        MATRIX_PUTS.append(urlparse(self.path).path)
        self._reply({"event_id": "$reply1"})


SLACK_POSTS = []


class MockSlackHttp(_JsonHandler):
    def do_POST(self):
        body = self._body()
        SLACK_POSTS.append((urlparse(self.path).path, body))
        if self.path.endswith("/apps.connections.open"):
            self._reply({"ok": True,
                         "url": "ws://127.0.0.1:9/unused"})
            return
        if self.path.endswith("/chat.postMessage"):
            self._reply({"ok": True, "ts": "222.333"})
            return
        self._reply({"ok": False, "error": "unknown_method"})


class MiniSlackSocket(threading.Thread):
    """One-connection RFC6455 server: pushes one events_api
    envelope, then records the adapter's ack frame."""

    def __init__(self):
        super().__init__(daemon=True)
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET,
                                 socket.SO_REUSEADDR, 1)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]
        self.acked = []

    def run(self):
        conn, _addr = self.listener.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        key = re.search(rb"Sec-WebSocket-Key: (\S+)",
                        data).group(1)
        accept = base64.b64encode(hashlib.sha1(
            key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            ).digest())
        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        envelope = {
            "envelope_id": "env-1", "type": "events_api",
            "payload": {"event": {
                "type": "message", "text": "hi slack",
                "user": "U42", "channel": "C9",
                "ts": "111.222"}}}
        payload = json.dumps(envelope).encode("utf-8")
        if len(payload) < 126:
            header = bytes([0x81, len(payload)])
        elif len(payload) < 65536:
            header = bytes([0x81, 126]) + len(payload).to_bytes(
                2, "big")
        else:
            header = bytes([0x81, 127]) + len(payload).to_bytes(
                8, "big")
        conn.sendall(header + payload)
        header = conn.recv(2)
        if len(header) == 2:
            masked = header[1] & 0x80
            size = header[1] & 0x7F
            mask = conn.recv(4) if masked else b""
            body = b""
            while len(body) < size:
                body += conn.recv(size - len(body))
            if masked:
                body = bytes(b ^ mask[i % 4]
                             for i, b in enumerate(body))
            self.acked.append(json.loads(body or b"{}"))
        time.sleep(2)
        try:
            conn.close()
        except OSError:
            pass


def main():
    server = HTTPServer(("127.0.0.1", 0), MockTelegram)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever,
                     daemon=True).start()
    api_base = f"http://127.0.0.1:{port}"

    os.makedirs(agentlib.secrets_dir(), exist_ok=True)
    secret_path = os.path.join(agentlib.secrets_dir(), "channel-tg")
    with open(secret_path, "w", encoding="utf-8") as handle:
        handle.write("mock-token\n")
    os.chmod(secret_path, 0o600)

    registry = {"skills": [], "mcp_servers": [], "connectors": [],
                "channels": [{
                    "id": "tg", "name": "TG", "kind": "telegram",
                    "enabled": True, "allowlist": ["111"],
                    "secret": "channel-tg",
                    "options": {"api_base": api_base},
                    "description": "", "license": ""}]}
    reg_path = os.path.join(ROOT, "registry.json")
    with open(reg_path, "w", encoding="utf-8") as handle:
        json.dump(registry, handle)
    os.environ["DCLAWOS_REGISTRY"] = reg_path

    # 1. channellib-level poll: allowlist filtering + cursor.
    entry = channellib.resolve_channel("tg")
    result = channellib.poll_channel(entry)
    if not result.get("ok"):
        fail(f"poll failed: {result}")
    events = result.get("events", [])
    if len(events) != 1 or events[0]["sender"] != "111" or \
            events[0]["chat"] != "555" or \
            events[0]["text"] != "hello channel":
        fail(f"allowlist filtering wrong: {events}")
    result2 = channellib.poll_channel(entry)
    if result2.get("events"):
        fail(f"cursor redelivered updates: {result2}")
    state = channellib._load_state("tg")
    if int(state.get("offset", 0)) < 12:
        fail(f"cursor did not advance past both updates: {state}")
    if int(state.get("dropped", 0)) != 1:
        fail(f"dropped count wrong: {state}")

    # 2. Daemon-level poll: task creation + pending reply routing.
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader(
        "dclawos_agent_mod_p5", "/usr/local/bin/dclawos-agent")
    spec = importlib.util.spec_from_loader("dclawos_agent_mod_p5",
                                            loader)
    agent_mod = importlib.util.module_from_spec(spec)
    loader.exec_module(agent_mod)
    UPDATES.append({"update_id": 20, "message": {
        "text": "second hello",
        "from": {"id": 111}, "chat": {"id": 555}}})
    before = len(agentlib.list_tasks())
    agent_mod._poll_channels()
    tasks_now = agentlib.list_tasks()
    if len(tasks_now) != before + 1:
        fail("channel poll did not create exactly one task")
    agent_mod._poll_channels()
    if len(agentlib.list_tasks()) != before + 1:
        fail("channel poll redelivered an event")
    pending = agent_mod._load_pending()
    if len(pending) != 1:
        fail(f"pending map wrong: {pending}")
    task_id, info = next(iter(pending.items()))
    if info.get("channel") != "tg" or info.get("chat") != "555":
        fail(f"pending route wrong: {info}")

    # 3. Reply flush: delivered to the originating chat only.
    task = agentlib.load_task(task_id)
    task["status"] = "done"
    task["result"] = {"reply": "channel reply"}
    agentlib.save_task(task)
    agent_mod._flush_pending_replies()
    if len(SENT) != 1 or str(SENT[0].get("chat_id")) != "555" or \
            SENT[0].get("text") != "channel reply":
        fail(f"reply not routed to origin chat: {SENT}")
    if agent_mod._load_pending():
        fail("pending map not drained after flush")

    # 4. Discord (module 2): cursor poll, allowlist, send.
    dserver = HTTPServer(("127.0.0.1", 0), MockDiscord)
    threading.Thread(target=dserver.serve_forever,
                     daemon=True).start()
    dbase = f"http://127.0.0.1:{dserver.server_address[1]}"
    _register_channel(
        {"id": "dc", "name": "DC", "kind": "discord",
         "enabled": True, "allowlist": ["42"],
         "secret": "channel-dc",
         "options": {"api_base": dbase, "channel_id": "900"},
         "description": "", "license": ""},
        {"channel-dc": "mock-token"})
    entry = channellib.resolve_channel("dc")
    result = channellib.poll_channel(entry)
    texts = [e["text"] for e in result.get("events", [])]
    if texts != ["first from owner", "second from owner"]:
        fail(f"discord poll wrong (order/filter): {result}")
    if channellib.poll_channel(entry).get("events"):
        fail("discord cursor redelivered messages")
    sent = channellib.send_message("dc", "900", "discord reply")
    if not sent.get("ok") or not DISCORD_SENT or \
            DISCORD_SENT[-1].get("content") != "discord reply":
        fail(f"discord send wrong: {sent} {DISCORD_SENT}")
    dserver.shutdown()

    # 5. Matrix (module 3): baseline poll, encrypted skip, send.
    mserver = HTTPServer(("127.0.0.1", 0), MockMatrix)
    threading.Thread(target=mserver.serve_forever,
                     daemon=True).start()
    mbase = f"http://127.0.0.1:{mserver.server_address[1]}"
    _register_channel(
        {"id": "mx", "name": "MX", "kind": "matrix",
         "enabled": True, "allowlist": ["@owner:test"],
         "secret": "channel-mx",
         "options": {"homeserver": mbase},
         "description": "", "license": ""},
        {"channel-mx": "mock-token"})
    entry = channellib.resolve_channel("mx")
    first = channellib.poll_channel(entry)
    if first.get("events"):
        fail(f"matrix baseline poll must emit nothing: {first}")
    second = channellib.poll_channel(entry)
    texts = [e["text"] for e in second.get("events", [])]
    if texts != ["hi matrix"]:
        fail(f"matrix second poll wrong: {second}")
    if channellib.poll_channel(entry).get("events"):
        fail("matrix sync redelivered events")
    sent = channellib.send_message("mx", "!r:test", "matrix reply")
    if not sent.get("ok") or not MATRIX_PUTS or \
            "/rooms/" not in MATRIX_PUTS[-1] or \
            "/send/m.room.message/" not in MATRIX_PUTS[-1]:
        fail(f"matrix send wrong: {sent} {MATRIX_PUTS}")
    mserver.shutdown()

    # 6. Slack (module 2): Socket Mode receive, ack, send.
    ws_server = MiniSlackSocket()
    ws_server.start()
    sserver = HTTPServer(("127.0.0.1", 0), MockSlackHttp)
    threading.Thread(target=sserver.serve_forever,
                     daemon=True).start()
    sbase = f"http://127.0.0.1:{sserver.server_address[1]}"
    _register_channel(
        {"id": "slk", "name": "SLK", "kind": "slack",
         "enabled": True, "allowlist": ["U42"],
         "secret": "channel-slk",
         "options": {"api_base": sbase,
                     "ws_base": f"ws://127.0.0.1:"
                                f"{ws_server.port}/"},
         "description": "", "license": ""},
        {"channel-slk": "xoxb-mock", "channel-slk-app": "xapp-mock"})
    entry = channellib.resolve_channel("slk")
    got = []
    deadline = time.time() + 20
    while time.time() < deadline and not got:
        result = channellib.poll_channel(entry)
        got = result.get("events", [])
        if not got:
            time.sleep(0.3)
    if len(got) != 1 or got[0]["text"] != "hi slack" or \
            got[0]["chat"] != "C9" or got[0]["sender"] != "U42":
        fail(f"slack receive wrong: {got}")
    if not ws_server.acked or \
            ws_server.acked[0].get("envelope_id") != "env-1":
        fail(f"slack envelope not acked: {ws_server.acked}")
    sent = channellib.send_message("slk", "C9", "slack reply")
    posts = [body for path, body in SLACK_POSTS
             if path.endswith("/chat.postMessage")]
    if not sent.get("ok") or not posts or \
            posts[-1].get("text") != "slack reply":
        fail(f"slack send wrong: {sent} {SLACK_POSTS}")
    sserver.shutdown()

    server.shutdown()
    print("channeltest OK: telegram/discord/slack/matrix adapters "
          "verified against local mocks (allowlist filtering, "
          "cursor once-only delivery, task creation, reply routing, "
          "socket-mode ack, matrix baseline + encrypted skip)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Slack channel adapter (Phase 5, WS1 — third channel).

Socket Mode: Slack's Events API needs a public HTTPS endpoint,
which a headless home machine does not have; Socket Mode is the
outbound-only alternative — the app opens a websocket to Slack
and receives events over it. Two tokens are required:

- bot token (xoxb-...): entry["secret"] names its secret file;
  used for chat.postMessage sends.
- app token (xapp-...): the secret file named entry["secret"] +
  "-app"; used for apps.connections.open.

Delivery model (differs from the poll channels, documented in
docs/PHASE5.md): a per-channel background receiver thread inside
the agent daemon keeps the socket open, ACKs every envelope, and
buffers message events; poll() drains the buffer. Socket Mode
does NOT backfill: messages sent while the machine was off are
not redelivered. Bot's own messages and message_changed/
message_deleted subtypes are skipped.

The websocket client is the `websocket-client` library (Debian
package python3-websocket, added to the image package lists in
Phase 5 module 2). If the library is missing the adapter reports
a clear error instead of pretending to work.

API/socket endpoints: options["api_base"] / options["ws_base"]
(or env DCLAWOS_SLACK_API_BASE) exist so the build gate can run
local mock Slack servers — the same override pattern as the
other adapters.

// reuse-check: OpenClaw extensions/slack NOT copied, reason: the
  upstream adapter is TypeScript on @slack/bolt + the gateway;
  the Socket Mode contract (connections.open, envelope ack,
  events_api payloads, chat.postMessage) is the transferable part
  and is implemented here in Python.
"""

import json
import os
import threading
import urllib.request

from channellib import ChannelError

API_BASE_DEFAULT = "https://slack.com/api"
HTTP_TIMEOUT = 20

_RECEIVERS = {}
_RECEIVERS_LOCK = threading.Lock()


def _api_base(ctx):
    options = ctx["entry"].get("options") or {}
    if options.get("api_base"):
        return str(options["api_base"]).rstrip("/")
    env = os.environ.get("DCLAWOS_SLACK_API_BASE")
    if env:
        return env.rstrip("/")
    return API_BASE_DEFAULT


def _post(ctx, method, payload, token):
    if not token:
        raise ChannelError(f"slack: missing token for {method}")
    request = urllib.request.Request(
        f"{_api_base(ctx)}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}",
                 "User-Agent": "DclawOS-agent/2.0"})
    try:
        with urllib.request.urlopen(request,
                                    timeout=HTTP_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except ChannelError:
        raise
    except Exception as exc:  # noqa: BLE001 - network faults are data
        raise ChannelError(f"slack {method} failed: {exc}")
    if not isinstance(body, dict) or not body.get("ok"):
        error = body.get("error") if isinstance(body, dict) else body
        raise ChannelError(f"slack {method} refused: {error}")
    return body


class _Receiver:
    """Background socket-mode receiver for one channel id."""

    def __init__(self, channel_id, ctx_factory):
        self.channel_id = channel_id
        self.ctx_factory = ctx_factory
        self.buffer = []
        self.lock = threading.Lock()
        self.thread = None
        self.stop_flag = False
        self.error = ""

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_flag = False
        self.thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"dclawos-slack-{self.channel_id}")
        self.thread.start()

    def _run(self):
        try:
            import websocket
        except ImportError:
            self.error = ("slack: websocket-client library missing "
                          "(Debian package python3-websocket)")
            return
        while not self.stop_flag:
            ctx = self.ctx_factory()
            try:
                app_token = ctx["app_token"]
                body = _post(ctx, "apps.connections.open", {},
                             app_token)
                ws_url = body.get("url")
                if not ws_url:
                    raise ChannelError(
                        "slack: connections.open returned no url")
                ws_options = (ctx["entry"].get("options") or {})
                if ws_options.get("ws_base"):
                    ws_url = ws_options["ws_base"]
                socket = websocket.create_connection(
                    ws_url, timeout=30)
                self.error = ""
                try:
                    self._loop(socket)
                finally:
                    try:
                        socket.close()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as exc:  # noqa: BLE001 - reconnect loop
                self.error = f"slack receiver: {exc}"
                import time
                time.sleep(5)

    def _loop(self, socket):
        while not self.stop_flag:
            raw = socket.recv()
            if not raw:
                return
            try:
                envelope = json.loads(raw)
            except ValueError:
                continue
            envelope_id = envelope.get("envelope_id")
            if envelope_id:
                socket.send(json.dumps({"envelope_id": envelope_id}))
            if envelope.get("type") == "disconnect":
                return  # Slack asks us to reconnect
            if envelope.get("type") != "events_api":
                continue
            event = (envelope.get("payload") or {}).get("event") or {}
            if event.get("type") != "message":
                continue
            if event.get("subtype") or event.get("bot_id"):
                continue
            text = event.get("text")
            user = event.get("user")
            channel = event.get("channel")
            if not isinstance(text, str) or not text or \
                    not user or not channel:
                continue
            with self.lock:
                self.buffer.append({
                    "id": str(event.get("ts", "")),
                    "text": text,
                    "sender": str(user),
                    "chat": str(channel),
                })

    def drain(self):
        with self.lock:
            out = self.buffer
            self.buffer = []
            return out


def _receiver_for(ctx):
    channel_id = ctx["entry"]["id"]
    with _RECEIVERS_LOCK:
        receiver = _RECEIVERS.get(channel_id)
        if receiver is None:
            def factory(ctx=ctx):
                built = dict(ctx)
                secret_name = ctx["entry"].get("secret") or ""
                import agentlib
                built["app_token"] = agentlib.read_secret(
                    secret_name + "-app") if secret_name else ""
                return built
            receiver = _Receiver(channel_id, factory)
            _RECEIVERS[channel_id] = receiver
        receiver.start()
        return receiver


def poll(ctx):
    receiver = _receiver_for(ctx)
    if receiver.error and not receiver.buffer:
        raise ChannelError(receiver.error)
    return receiver.drain()


def send(ctx, chat_id, text):
    if not chat_id:
        return {"ok": False,
                "error": "slack: no chat (channel id) to reply to"}
    if not isinstance(text, str) or not text:
        return {"ok": False, "error": "slack: empty message"}
    body = _post(ctx, "chat.postMessage",
                 {"channel": chat_id, "text": text}, ctx["token"])
    return {"ok": True, "chat": chat_id, "message_id": body.get("ts")}

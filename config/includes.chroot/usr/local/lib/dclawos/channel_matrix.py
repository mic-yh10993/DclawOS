#!/usr/bin/env python3
"""Matrix channel adapter (Phase 5, WS1 — fourth channel).

Matrix is the other token-based, daemon-free protocol in the
reference set: a user access token + the client-server API's
/sync long-poll. No public endpoint is needed (outbound only),
which fits a headless home machine.

Configuration: options["homeserver"] is the server base URL
(e.g. https://matrix.org). The token (entry["secret"]) is a
Matrix access token for the account the agent speaks as; its
user id is learned once via /account/whoami and cached in state.

- poll: GET /_matrix/client/v3/sync?since=<state.next_batch>
  &timeout=0. Events are m.room.message / m.text entries from
  joined rooms; the agent's own messages are skipped. The FIRST
  poll only establishes next_batch and emits nothing — history
  backlog is not replayed into agent tasks.
- send: PUT .../rooms/<roomId>/send/m.room.message/<txnId>
  with {msgtype: "m.text", body}. One transaction per send.

Encryption note (honest): this adapter speaks the PLAIN
client-server API. End-to-end encrypted rooms deliver ciphertext
(m.room.encrypted) that this adapter cannot read — such events
advance the cursor without becoming tasks. Use unencrypted DMs
for the agent channel. (OpenClaw's Matrix extension bundles the
crypto SDKs; porting E2EE is out of stdlib reach and is recorded
as a named boundary in docs/PHASE5.md.)

// reuse-check: OpenClaw extensions/matrix NOT copied, reason:
  the upstream adapter is TypeScript on matrix-js-sdk with the
  crypto SDKs; the client-server sync contract (since-token
  cursor, timeline events, send transaction) is the transferable
  part and is implemented here in stdlib Python.
"""

import json
import urllib.parse
import urllib.request

from channellib import ChannelError

HTTP_TIMEOUT = 30
API_PREFIX = "/_matrix/client/v3"


def _homeserver(ctx):
    options = ctx["entry"].get("options") or {}
    base = options.get("homeserver")
    if not base:
        raise ChannelError(
            "matrix: options.homeserver is required")
    return str(base).rstrip("/")


def _call(ctx, method, path, payload=None, params=None):
    token = ctx["token"]
    if not token:
        raise ChannelError("matrix: no access token configured")
    url = _homeserver(ctx) + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = None
    headers = {"Authorization": f"Bearer {token}",
               "User-Agent": "DclawOS-agent/2.0"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data,
                                     headers=headers, method=method)
    try:
        with urllib.request.urlopen(request,
                                    timeout=HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - network faults are data
        raise ChannelError(f"matrix {method} {path} failed: {exc}")
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise ChannelError(f"matrix {method} {path}: bad JSON")


def _own_user_id(ctx):
    state = ctx["state"]
    if not state.get("user_id"):
        body = _call(ctx, "GET", API_PREFIX + "/account/whoami")
        user_id = body.get("user_id")
        if not user_id:
            raise ChannelError("matrix: whoami returned no user_id")
        state["user_id"] = str(user_id)
        ctx["save_state"]()
    return state["user_id"]


def poll(ctx):
    state = ctx["state"]
    own = _own_user_id(ctx)
    since = state.get("next_batch")
    params = {"timeout": 0}
    if since:
        params["since"] = since
    body = _call(ctx, "GET", API_PREFIX + "/sync", params=params)
    next_batch = body.get("next_batch")
    if next_batch:
        state["next_batch"] = str(next_batch)
    if not since:
        # First contact: establish the cursor only; the response
        # is backlog history, not new owner messages.
        ctx["save_state"]()
        return []
    events = []
    rooms = ((body.get("rooms") or {}).get("join") or {})
    for room_id, room in rooms.items():
        timeline = ((room or {}).get("timeline") or {})
        for event in timeline.get("events") or []:
            if not isinstance(event, dict):
                continue
            if event.get("type") != "m.room.message":
                continue
            content = event.get("content") or {}
            if content.get("msgtype") != "m.text":
                continue
            sender = str(event.get("sender", ""))
            if sender == own:
                continue
            text = content.get("body")
            if not isinstance(text, str) or not text:
                continue
            events.append({
                "id": str(event.get("event_id", "")),
                "text": text,
                "sender": sender,
                "chat": str(room_id),
            })
    ctx["save_state"]()
    return events


def send(ctx, chat_id, text):
    if not chat_id:
        return {"ok": False,
                "error": "matrix: no chat (room id) to reply to"}
    if not isinstance(text, str) or not text:
        return {"ok": False, "error": "matrix: empty message"}
    import time
    txn = f"dclawos-{int(time.time() * 1000)}"
    path = (API_PREFIX + "/rooms/" +
            urllib.parse.quote(chat_id, safe="") +
            "/send/m.room.message/" + urllib.parse.quote(txn,
                                                          safe=""))
    body = _call(ctx, "PUT", path,
                 payload={"msgtype": "m.text", "body": text})
    return {"ok": True, "chat": chat_id,
            "message_id": body.get("event_id")}

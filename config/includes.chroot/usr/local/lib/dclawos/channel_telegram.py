#!/usr/bin/env python3
"""Telegram channel adapter (Phase 5, WS1 — first channel).

Bot API over plain HTTPS, long-poll style with a zero-second
server timeout (the daemon polls on its own cycle; a held-open
getUpdates request would fight the daemon's single poll loop):

- poll: POST getUpdates {offset: state.cursor + 1} — every update
  is consumed exactly once by advancing state["offset"] past it
  (adapter owns the cursor, the connector-contract rule). Only
  plain text messages become events; other update kinds (photos,
  joins, edits) advance the cursor without an event. The last
  chat a message arrived from is remembered as state["last_chat"]
  so outbound sends without an explicit chat have a destination.
- send: POST sendMessage {chat_id, text}; text is chunked at
  4000 chars (Bot API limit is 4096). chat_id falls back to
  state["last_chat"], then options["default_chat"]; with none of
  them the send fails honestly — a bot cannot invent a chat.

API base: entry options["api_base"] wins, then env
DCLAWOS_TELEGRAM_API_BASE, then https://api.telegram.org. The
override exists so the build gate can run a local mock Bot API
server (no real token, no external network) — the same pattern
as llmlib's DCLAWOS_LLM_BASE_URL_<PROVIDER>.

Token: ctx["token"] (resolved by channellib from the channel's
secret file). It appears only in request URLs to the API base,
never in return values, state, or logs.

// reuse-check: OpenClaw extensions/telegram NOT copied, reason:
  the upstream adapter is TypeScript on grammY inside the
  gateway; the Bot API contract (getUpdates offset semantics,
  sendMessage) is the transferable part and is implemented here
  in stdlib Python. Allowlist matching follows OpenClaw's
  allowFrom semantics (src/channels/allow-from.ts) as enforced
  centrally by channellib.
"""

import json
import urllib.request

from channellib import ChannelError

API_BASE_DEFAULT = "https://api.telegram.org"
HTTP_TIMEOUT = 20
SEND_CHUNK = 4000


def _api_base(ctx):
    options = ctx["entry"].get("options") or {}
    if options.get("api_base"):
        return str(options["api_base"]).rstrip("/")
    import os
    env = os.environ.get("DCLAWOS_TELEGRAM_API_BASE")
    if env:
        return env.rstrip("/")
    return API_BASE_DEFAULT


def _call(ctx, method, payload):
    token = ctx["token"]
    if not token:
        raise ChannelError("telegram: no bot token configured")
    url = f"{_api_base(ctx)}/bot{token}/{method}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "DclawOS-agent/2.0"})
    try:
        with urllib.request.urlopen(request,
                                    timeout=HTTP_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except ChannelError:
        raise
    except Exception as exc:  # noqa: BLE001 - network faults are data
        raise ChannelError(f"telegram {method} failed: {exc}")
    if not isinstance(body, dict) or not body.get("ok"):
        description = ""
        if isinstance(body, dict):
            description = str(body.get("description", ""))
        raise ChannelError(
            f"telegram {method} refused: {description or body}")
    return body.get("result")


def poll(ctx):
    state = ctx["state"]
    offset = int(state.get("offset", 0))
    updates = _call(ctx, "getUpdates", {
        "offset": offset, "timeout": 0,
        "allowed_updates": ["message"],
    }) or []
    events = []
    for update in updates:
        if not isinstance(update, dict):
            continue
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            offset = max(offset, update_id + 1)
        message = update.get("message")
        if not isinstance(message, dict):
            continue
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is not None:
            state["last_chat"] = str(chat_id)
        text = message.get("text")
        if not isinstance(text, str) or not text:
            continue
        sender = (message.get("from") or {}).get("id")
        events.append({
            "id": str(update_id),
            "text": text,
            "sender": str(sender if sender is not None else ""),
            "chat": str(chat_id if chat_id is not None else ""),
        })
    state["offset"] = offset
    ctx["save_state"]()
    return events


def send(ctx, chat_id, text):
    state = ctx["state"]
    target = chat_id or state.get("last_chat") or \
        (ctx["entry"].get("options") or {}).get("default_chat")
    if not target:
        return {"ok": False,
                "error": "telegram: no chat known yet (nobody has "
                         "messaged the bot and no default_chat set)"}
    if not isinstance(text, str) or not text:
        return {"ok": False, "error": "telegram: empty message"}
    last = None
    for index in range(0, len(text), SEND_CHUNK):
        last = _call(ctx, "sendMessage", {
            "chat_id": target,
            "text": text[index:index + SEND_CHUNK],
        })
    message_id = last.get("message_id") if isinstance(last, dict) \
        else None
    return {"ok": True, "chat": str(target), "message_id": message_id}

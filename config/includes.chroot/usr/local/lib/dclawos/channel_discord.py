#!/usr/bin/env python3
"""Discord channel adapter (Phase 5, WS1 — second channel).

Bot API over plain HTTPS REST — no gateway websocket: a bot that
only needs one conversation surface can poll one channel's
message list, which fits the daemon's poll cycle and a headless
home machine (outbound connections only).

Configuration (entry options):
- channel_id (REQUIRED): the Discord channel the bot reads and
  replies in — a guild text channel the bot may view, or a DM
  channel id. Discord has no "list my DMs" polling surface for
  bots, so the channel is explicit configuration, not discovery.
- api_base: mock override (options, then env
  DCLAWOS_DISCORD_API_BASE, then https://discord.com/api/v10).

- poll: GET /channels/<id>/messages?after=<cursor>&limit=50.
  Discord returns newest first; events are emitted oldest first.
  The bot's own messages are skipped (its user id is learned once
  via GET /users/@me and kept in state). The cursor is the newest
  message snowflake consumed — adapter-owned, exactly-once.
- send: POST /channels/<id>/messages {content}; content chunked
  at 2000 chars (Discord's message limit). The reply target is
  the configured channel (events carry it as their chat).

Token: ctx["token"] (Bot token), sent as "Authorization: Bot
<token>"; never in return values or state.

// reuse-check: OpenClaw extensions/discord NOT copied, reason:
  the upstream adapter is TypeScript driving the gateway
  websocket + interactions framework; the REST message contract
  (list-after-cursor, post message) is the transferable part and
  is implemented here in stdlib Python.
"""

import json
import urllib.request

from channellib import ChannelError

API_BASE_DEFAULT = "https://discord.com/api/v10"
HTTP_TIMEOUT = 20
SEND_CHUNK = 2000


def _api_base(ctx):
    options = ctx["entry"].get("options") or {}
    if options.get("api_base"):
        return str(options["api_base"]).rstrip("/")
    import os
    env = os.environ.get("DCLAWOS_DISCORD_API_BASE")
    if env:
        return env.rstrip("/")
    return API_BASE_DEFAULT


def _channel_id(ctx):
    options = ctx["entry"].get("options") or {}
    channel_id = options.get("channel_id")
    if not channel_id:
        raise ChannelError(
            "discord: options.channel_id is required (the channel "
            "the bot reads and replies in)")
    return str(channel_id)


def _call(ctx, method, path, payload=None):
    token = ctx["token"]
    if not token:
        raise ChannelError("discord: no bot token configured")
    data = None
    headers = {"Authorization": f"Bot {token}",
               "User-Agent": "DclawOS-agent/2.0"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        _api_base(ctx) + path, data=data, headers=headers,
        method=method)
    try:
        with urllib.request.urlopen(request,
                                    timeout=HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - network faults are data
        raise ChannelError(f"discord {method} {path} failed: {exc}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise ChannelError(f"discord {method} {path}: bad JSON")


def poll(ctx):
    state = ctx["state"]
    channel_id = _channel_id(ctx)
    if not state.get("bot_id"):
        me = _call(ctx, "GET", "/users/@me")
        if isinstance(me, dict) and me.get("id"):
            state["bot_id"] = str(me["id"])
    cursor = str(state.get("cursor", "0"))
    messages = _call(
        ctx, "GET",
        f"/channels/{channel_id}/messages?after={cursor}&limit=50")
    if messages is None:
        messages = []
    if not isinstance(messages, list):
        raise ChannelError("discord: message list is not a list")
    events = []
    newest = int(cursor)
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        message_id = message.get("id")
        if message_id is not None:
            try:
                newest = max(newest, int(message_id))
            except (TypeError, ValueError):
                pass
        author = message.get("author") or {}
        author_id = str(author.get("id", ""))
        if author.get("bot") or author_id == state.get("bot_id"):
            continue
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        events.append({
            "id": str(message_id),
            "text": content,
            "sender": author_id,
            "chat": channel_id,
        })
    state["cursor"] = str(newest)
    ctx["save_state"]()
    return events


def send(ctx, chat_id, text):
    target = chat_id or _channel_id(ctx)
    if not isinstance(text, str) or not text:
        return {"ok": False, "error": "discord: empty message"}
    last = None
    for index in range(0, len(text), SEND_CHUNK):
        last = _call(ctx, "POST", f"/channels/{target}/messages",
                     {"content": text[index:index + SEND_CHUNK]})
    message_id = last.get("id") if isinstance(last, dict) else None
    return {"ok": True, "chat": str(target), "message_id": message_id}

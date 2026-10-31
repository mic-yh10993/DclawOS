#!/usr/bin/env python3
"""Chat host logic (Phase 6 module M3). Headless, stdlib + local libs.

This is the typed host side of the chat WebView: dclawos-chat (the
GTK/WebKit window) is a thin shell around ChatHost here, so the
whole bridge surface can be gate-tested headlessly (chattest.py)
without a display. The page is untrusted; every verb arrives
already validated by bridgeprotocol and lands in dispatch().

What lives here, and where it came from:
- parse_command: the pre-M3 chat window's slash-command parser,
  transplanted with its semantics unchanged (same titles, goals
  and step structures); side-effect outputs (help text, errors,
  /stop) become structured results the host/page render.
- Task creation: agentlib.new_task/save_task, llm tasks bound to
  the persistent conversation (convlib), exactly as the old
  window did; the current session id persists in
  ~/.config/dclawos/chat-session (16-hex, convlib.SESSION_ID_RE).
- Inline approval verdicts: the dclawos-approve write path copied
  field-for-field (status, approved_by="human-gui", approved_at,
  ticketlib.save_ticket) — there is no second verdict channel.
  Approval cards show the ticket's operation + summary plus the
  params the approval EVENT carried, which agentlib redacted
  (bridgeprotocol.redact_params) before publishing; card data is
  additionally passed through bridgeprotocol.strip_secrets when
  it is serialised into a bridge reply/event.
- Model switcher: the merged catalog view is registrylib
  .merged_providers over the engine catalog (the same merge the
  M2 settings host serves); writes go to the shared settings
  file via settingslib.apply_setting (ai-provider / ai-model),
  the same store M2 writes.
- Stop: the existing cooperative stop mechanism only —
  dclawos-agent-ctl stop (the agent-stop flag the daemon checks),
  the same call the old window's /stop made.
- Event filtering: bus events are filtered HOST-side by the task
  ids / task sessions / conversation this window created, so the
  page never receives the full event firehose.

// reuse-check: parse_command, session-file handling, task
  creation, copy feedback semantics and the /stop call are
  transplanted from the pre-M3 dclawos-chat window (this module
  replaces that window's logic); no OpenClaw code is involved.
// reuse-check: approval verdict fields ADOPTED verbatim from
  dclawos-approve.on_verdict (status / approved_by / approved_at
  + ticketlib.save_ticket); the ticket trust model is untouched.
// reuse-check: catalog merge + settings writes ADOPTED from the
  M2 settings host (registrylib.merged_providers,
  settingslib.apply_setting); no parallel model store exists.
"""

import json
import os
import secrets as _secrets
import shlex
import subprocess
import time

import agentlib
import bridgeprotocol
import convlib
import registrylib
import settingslib
import ticketlib

HELP_TEXT = (
    "命令：/run /read /write /skill /mcp /connector /route "
    "/subagent /takeover /stop；普通文字创建任务（无步骤时如实记录）。"
)

CATALOG_DEFAULT = "/usr/share/dclawos/engine/catalog.json"
LAUNCH_TARGETS = {
    "dashboard": "dclawos-dashboard",
    "monitor": "dclawos-monitor",
    "settings": "dclawos-settings",
}


def catalog_path():
    return os.environ.get("DCLAWOS_ENGINE_CATALOG", CATALOG_DEFAULT)


def load_catalog():
    """Engine catalog providers list; [] when absent (M2 pattern)."""
    try:
        with open(catalog_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        providers = data.get("providers")
        if isinstance(providers, list):
            return providers
    except (OSError, ValueError):
        pass
    return []


def session_file_path():
    base = os.environ.get("XDG_CONFIG_HOME") or \
        os.path.expanduser("~/.config")
    return os.path.join(base, "dclawos", "chat-session")


def load_conv_session():
    """The window's persistent conversation id (pre-M3 semantics)."""
    try:
        with open(session_file_path(), "r", encoding="utf-8") as handle:
            value = handle.read().strip()
        if convlib.SESSION_ID_RE.match(value):
            return value
    except OSError:
        pass
    value = _secrets.token_hex(8)
    persist_conv_session(value)
    return value


def persist_conv_session(value):
    try:
        os.makedirs(os.path.dirname(session_file_path()),
                    exist_ok=True)
        with open(session_file_path(), "w", encoding="utf-8") as handle:
            handle.write(value)
    except OSError:
        pass


def parse_command(text):
    """Parse a /command line (pre-M3 semantics, transplanted).

    Returns a dict:
      {"action": "task", "title", "goal", "steps"}  — create a task
      {"action": "reply", "text"}                   — show locally
      {"action": "stop"}                            — /stop
    """
    try:
        parts = shlex.split(text)
    except ValueError as exc:
        return {"action": "reply", "text": f"命令解析失败：{exc}"}
    if not parts:
        return {"action": "reply", "text": "空命令"}
    cmd, args = parts[0], parts[1:]
    if cmd == "/help":
        return {"action": "reply", "text": HELP_TEXT}
    if cmd == "/stop":
        return {"action": "stop"}
    if cmd == "/run" and args:
        return {"action": "task", "title": "shell 命令",
                "goal": " ".join(args),
                "steps": [{"tool": "shell", "params": {"argv": args}}]}
    if cmd == "/read" and len(args) == 1:
        return {"action": "task", "title": "读取文件", "goal": args[0],
                "steps": [{"tool": "read_file",
                           "params": {"path": args[0]}}]}
    if cmd == "/write" and len(args) >= 2:
        path = args[0]
        raw = text.split(None, 2)
        content = raw[2] if len(raw) > 2 else ""
        return {"action": "task", "title": "写入文件", "goal": path,
                "steps": [{"tool": "write_file",
                           "params": {"path": path,
                                      "content": content}}]}
    if cmd == "/skill" and args:
        raw = text.split(None, 2)
        return {"action": "task", "title": "运行 Skill", "goal": args[0],
                "steps": [{"tool": "skill_run",
                           "params": {"skill": args[0],
                                      "input": raw[2]
                                      if len(raw) > 2 else ""}}]}
    if cmd == "/mcp" and len(args) >= 2:
        raw = text.split(None, 3)
        try:
            call_args = json.loads(raw[3]) if len(raw) > 3 else {}
        except ValueError as exc:
            return {"action": "reply",
                    "text": f"mcp 参数不是合法 JSON：{exc}"}
        return {"action": "task", "title": "调用 MCP",
                "goal": f"{args[0]}.{args[1]}",
                "steps": [{"tool": "mcp_call",
                           "params": {"server": args[0],
                                      "tool": args[1],
                                      "arguments": call_args}}]}
    if cmd == "/connector" and len(args) >= 2:
        raw = text.split(None, 2)
        return {"action": "task", "title": "发送连接器消息",
                "goal": args[0],
                "steps": [{"tool": "connector_send",
                           "params": {"connector": args[0],
                                      "message": raw[2]
                                      if len(raw) > 2 else ""}}]}
    if cmd == "/route":
        return {"action": "task", "title": "路由决策",
                "goal": "route_decide",
                "steps": [{"tool": "route_decide", "params": {}}]}
    if cmd == "/subagent" and args:
        goal = text.split(None, 1)[1]
        return {"action": "task", "title": "指派子代理", "goal": goal,
                "steps": [{"tool": "subagent",
                           "params": {"goal": goal, "steps": []}}]}
    if cmd == "/takeover" and len(args) >= 2:
        raw = text.split(None, 2)
        return {"action": "task", "title": "请求人工接管",
                "goal": args[0],
                "steps": [{"tool": "takeover_request",
                           "params": {"kind": args[0],
                                      "message": raw[2]
                                      if len(raw) > 2 else ""}}]}
    return {"action": "reply",
            "text": f"未知或参数不足的命令：{cmd}（/help 看列表）"}


def project_history(session_id):
    """Conversation history for the page: message entries only,
    {role, content} in transcript order."""
    out = []
    try:
        entries = convlib.load_entries(session_id)
    except (OSError, ValueError):
        return out
    for entry in entries:
        if entry.get("kind") != "message":
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content:
            continue
        role = message.get("role")
        if role in ("user", "assistant"):
            out.append({"role": role, "content": content})
    return out


def list_pending_tickets():
    """Pending, unexpired tickets (dclawos-approve.list_pending)."""
    out = []
    try:
        names = os.listdir(ticketlib.tickets_dir())
    except OSError:
        return out
    for name in sorted(names):
        if not name.endswith(".json"):
            continue
        try:
            ticket = ticketlib.load_ticket(name[:-5])
        except (OSError, ValueError):
            continue
        if ticket.get("status") == "pending" and \
                not ticketlib.is_expired(ticket):
            out.append(ticket)
    return out


class ChatHost:
    """Typed bridge handlers + bus-event filtering for one chat
    window. clipboard_fn / launch_fn are injectable (the GTK shell
    supplies the real clipboard / Popen; tests use the defaults,
    which record instead of touching the desktop)."""

    def __init__(self, conv_session=None, clipboard_fn=None,
                 launch_fn=None):
        self.conv_session = conv_session or load_conv_session()
        convlib.ensure_session(self.conv_session)
        self.known_tasks = {}       # task id -> task session
        self.reconciled = set()     # task ids already finalised
        self.approvals = {}         # ticket id -> card dict
        self.last_reply = ""
        self.clipboard_text = ""
        self._clipboard_fn = clipboard_fn
        self._launch_fn = launch_fn
        for ticket in list_pending_tickets():
            self.approvals[ticket["id"]] = {
                "ticket_id": ticket["id"],
                "tool": ticket.get("operation", ""),
                "tier": None,
                "summary": ticket.get("params_summary", ""),
                "params": {},
                "status": "pending",
            }

    # ------------------------------------------------------------ state

    def model_state(self):
        import llmlib
        data, _skipped = registrylib.load_registry()
        merged = registrylib.merged_providers(load_catalog(), data)
        keys = llmlib.available_keys()
        providers = []
        for entry in merged:
            providers.append({
                "id": entry["id"], "name": entry.get("name", ""),
                "enabled": bool(entry.get("enabled", True)),
                "builtin": bool(entry.get("builtin")),
                "format": entry.get("format", ""),
                "key_configured": entry["id"] in keys,
                "models": [{"id": m["id"]} for m in
                           entry.get("models", [])
                           if isinstance(m, dict) and m.get("id")],
            })
        settings = settingslib.load_settings()
        return {
            "provider": settings.get("ai-provider", ""),
            "model": settings.get("ai-model", ""),
            "configured": bool(keys),
            "providers": providers,
        }

    def state(self):
        sessions = convlib.list_sessions()
        return {
            "sessions": [{"id": s.get("id"),
                          "title": s.get("title", ""),
                          "updated_at": s.get("updated_at", 0)}
                         for s in sessions],
            "current_session": self.conv_session,
            "messages": project_history(self.conv_session),
            "model": self.model_state(),
            "approvals": [self._card_view(c) for c in
                          self.approvals.values()
                          if c.get("status") == "pending"],
            "help": HELP_TEXT,
        }

    def _card_view(self, card):
        # Params cross to the page ONLY through redact_params
        # (secret-shaped values replaced by "<redacted>", structure
        # kept); the bridge's make_reply additionally strips
        # secret-shaped keys from the serialised reply, so a secret
        # value cannot reach the page through either layer.
        return {
            "ticket_id": card.get("ticket_id"),
            "tool": card.get("tool"),
            "tier": card.get("tier"),
            "summary": card.get("summary"),
            "params": bridgeprotocol.redact_params(
                card.get("params") or {}),
            "status": card.get("status"),
        }

    # ------------------------------------------------------------ tasks

    def create_task(self, title, goal, steps, kind="steps"):
        task = agentlib.new_task(title, goal, steps)
        if kind == "llm":
            task["kind"] = "llm"
            task["conv_session"] = self.conv_session
        agentlib.save_task(task)
        self.known_tasks[task["id"]] = task.get("session", "")
        return task

    def do_stop(self):
        """Existing cooperative stop only (dclawos-agent-ctl stop,
        the same call the old window's /stop and /stop button made;
        the daemon honours the flag before each action)."""
        try:
            if self._launch_fn is not None:
                self._launch_fn(["dclawos-agent-ctl", "stop"])
            else:
                subprocess.Popen(["dclawos-agent-ctl", "stop"])
            return {"stopped": True,
                    "message": "已请求停止 agent 服务（协作式停止标志）"}
        except OSError as exc:
            raise ValueError(f"停止失败：{exc}")

    # --------------------------------------------------------- dispatch

    def dispatch(self, verb, params):
        params = params or {}
        if verb == "ping":
            return {"pong": True}
        if verb == "hello":
            return bridgeprotocol.make_hello_ack("chat")
        if verb == "chat.state":
            return self.state()
        if verb == "chat.history":
            session = params.get("session") or self.conv_session
            if not convlib.SESSION_ID_RE.match(str(session)):
                raise ValueError("会话 id 无效")
            return {"session": session,
                    "messages": project_history(session)}
        if verb == "chat.switch":
            session = params.get("session")
            if not isinstance(session, str) or \
                    not convlib.SESSION_ID_RE.match(session):
                raise ValueError("会话 id 无效")
            self.conv_session = session
            convlib.ensure_session(session)
            persist_conv_session(session)
            return {"session": session,
                    "messages": project_history(session)}
        if verb == "chat.new":
            session = _secrets.token_hex(8)
            convlib.ensure_session(session, title="会话")
            self.conv_session = session
            persist_conv_session(session)
            return {"session": session, "messages": []}
        if verb == "chat.send":
            return self._send(params)
        if verb == "chat.assign":
            return self._assign(params)
        if verb == "chat.model.set":
            provider = params.get("provider")
            model = params.get("model") or ""
            if not isinstance(provider, str) or not provider:
                raise ValueError("chat.model.set 需要 provider")
            settingslib.apply_setting("ai-provider", provider)
            settingslib.apply_setting("ai-model", str(model))
            return {"model": self.model_state()}
        if verb in ("chat.approve", "chat.deny"):
            verdict = "approved" if verb == "chat.approve" else "denied"
            return self._verdict(params.get("ticket_id"), verdict)
        if verb == "chat.stop":
            return self.do_stop()
        if verb == "chat.copy":
            text = params.get("text")
            if not isinstance(text, str) or not text:
                text = self.last_reply
            if not text:
                raise ValueError("还没有可复制的 AI 回答")
            self.clipboard_text = text
            if self._clipboard_fn is not None:
                self._clipboard_fn(text)
            return {"copied": True, "length": len(text)}
        if verb == "chat.launch":
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
            return {"launched": target}
        raise ValueError(f"unhandled chat verb: {verb!r}")

    def _send(self, params):
        text = params.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("消息为空")
        text = text.strip()
        if text.startswith("/"):
            parsed = parse_command(text)
            if parsed["action"] == "reply":
                return {"action": "reply", "text": parsed["text"]}
            if parsed["action"] == "stop":
                result = self.do_stop()
                return {"action": "reply", "text": result["message"]}
            task = self.create_task(parsed["title"], parsed["goal"],
                                    parsed["steps"])
            return {"action": "task", "task_id": task["id"],
                    "title": parsed["title"]}
        task = self.create_task(text[:80], text, [], kind="llm")
        return {"action": "task", "task_id": task["id"],
                "title": text[:80]}

    def _assign(self, params):
        title = params.get("title") or "指派任务"
        goal = params.get("goal") or ""
        steps = params.get("steps")
        if steps is None:
            steps = []
        if not isinstance(steps, list):
            raise ValueError("步骤必须是 JSON 数组")
        task = self.create_task(str(title), str(goal), steps)
        return {"action": "task", "task_id": task["id"],
                "title": str(title)}

    def _verdict(self, ticket_id, verdict):
        """The dclawos-approve verdict write, field-for-field:
        load ticket, require pending, set status / approved_by /
        approved_at, save via ticketlib. Nothing else grants or
        executes anything — the broker still binds execution to
        the ticket's operation + params hash."""
        if not isinstance(ticket_id, str) or \
                not ticketlib.TICKET_ID_RE.match(ticket_id):
            raise ValueError("票据 id 无效")
        try:
            ticket = ticketlib.load_ticket(ticket_id)
        except (OSError, ValueError) as exc:
            raise ValueError(f"读取票据失败：{exc}")
        if ticket.get("status") != "pending":
            raise ValueError("该票据已不在待审批状态")
        ticket["status"] = verdict
        ticket["approved_by"] = "human-gui"
        ticket["approved_at"] = int(time.time())
        ticketlib.save_ticket(ticket)
        card = self.approvals.get(ticket_id)
        if card is not None:
            card["status"] = verdict
        return {"ticket_id": ticket_id, "status": verdict}

    # ------------------------------------------------------------ events

    def note_approval_event(self, event):
        """Record an approval card from a bus approval event.
        Params arrive already redacted by agentlib; the card view
        strips secret-looking keys again before serialising."""
        data = event.get("data") or {}
        ticket_id = data.get("ticket_id")
        if not isinstance(ticket_id, str) or \
                not ticketlib.TICKET_ID_RE.match(ticket_id):
            return None
        if data.get("verdict") in ("approved", "denied"):
            card = self.approvals.get(ticket_id)
            if card is not None:
                card["status"] = data["verdict"]
            return {"type": "approval",
                    "data": {"ticket_id": ticket_id,
                             "status": data["verdict"]}}
        card = {
            "ticket_id": ticket_id,
            "tool": data.get("tool", ""),
            "tier": data.get("tier"),
            "summary": data.get("summary") or event.get("summary", ""),
            "params": data.get("params") or {},
            "status": "pending",
        }
        self.approvals[ticket_id] = card
        return {"type": "approval", "data": self._card_view(card)}

    def handle_bus_event(self, event):
        """Filter one bus event for this window; returns a list of
        bridge-event dicts {type, data} to push to the page (empty
        when the event is not this conversation's business). Task
        completion is reconciled here: a relevant event for a known
        task triggers ONE load of that task file (event-driven
        re-read, the M2 host pattern — not a timer poll), and a
        finished task yields exactly one chat.task_done event."""
        if not isinstance(event, dict):
            return []
        data = event.get("data") or {}
        if isinstance(data, dict) and data.get("display") is False:
            return []
        etype = event.get("type", "")
        if etype == "approval":
            forwarded = self.note_approval_event(event)
            return [forwarded] if forwarded else []
        task_id = event.get("task_id") or (
            data.get("task_id") if isinstance(data, dict) else None)
        session = event.get("session", "")
        conv = data.get("conv_session") if isinstance(data, dict) \
            else None
        relevant = (
            (task_id and task_id in self.known_tasks)
            or (session and session in self.known_tasks.values())
            or (conv and conv == self.conv_session))
        if not relevant:
            return []
        out = [{"type": etype,
                "data": bridgeprotocol.strip_secrets({
                    "summary": event.get("summary", ""),
                    "session": session, "task_id": task_id,
                    "data": data})}]
        # Reconcile a finished known task exactly once.
        candidate = task_id if task_id in self.known_tasks else None
        if candidate is None:
            for tid, tsession in self.known_tasks.items():
                if session and tsession == session:
                    candidate = tid
                    break
        if candidate and candidate not in self.reconciled:
            try:
                task = agentlib.load_task(candidate)
            except (OSError, ValueError):
                task = None
            if task is not None and task.get("status") in (
                    "done", "failed", "cancelled"):
                self.reconciled.add(candidate)
                result = task.get("result") or {}
                if isinstance(result, dict) and result.get("reply"):
                    self.last_reply = result["reply"]
                out.append({
                    "type": "chat.task_done",
                    "data": {
                        "task_id": candidate,
                        "status": task.get("status"),
                        "kind": task.get("kind"),
                        "title": task.get("title", ""),
                        "result": bridgeprotocol.strip_secrets(
                            result if isinstance(result, dict)
                            else {}),
                        "error": task.get("error"),
                    }})
        return out

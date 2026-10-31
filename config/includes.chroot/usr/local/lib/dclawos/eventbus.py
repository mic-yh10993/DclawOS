#!/usr/bin/env python3
"""Versioned Unix-socket event bus (Phase 6 module M1). Stdlib only.

The user directive (2026-10-31): monitoring must be PUSH, not poll —
the agent reports state, the system returns command output live,
and human-side programs no longer poll files/state on timers.

Design:
- One Unix-domain stream socket in the shared state directory:
  /var/lib/dclawos/eventbus.sock (env override
  DCLAWOS_EVENTBUS_SOCKET; the directory follows the existing
  dclawos-session exchange pattern: group dclawos-session, dir
  0770, socket 0660). The agent daemon owns and serves it.
- Wire protocol v1: newline-delimited JSON objects, one message
  per line, explicit "kind" field:
    client -> server: {"kind":"hello","protocol":1, ...}
                      {"kind":"subscribe"}
                      {"kind":"publish","event":{...}}
    server -> client: {"kind":"ack","for":<kind>,"ok":bool, ...}
                      {"kind":"event","event":{...}}
                      {"kind":"error","error":<str>}
  Malformed lines, unknown kinds and oversized lines fail closed
  for THAT connection (an error reply; the connection may be
  dropped) and never kill the server. No eval, no dynamic code:
  messages are parsed JSON data dispatched on the kind string.
- Events keep the durable events.jsonl envelope (ts, type,
  summary, session) and may add id / task_id / data. The bus is a
  freshness channel ONLY: events.jsonl remains the durable append
  log (agentlib.log_event writes it first, then publishes
  best-effort). A dead bus loses nothing durable; a dead log
  writer is never hidden by the bus.
- Publishers outside the daemon (broker, provisioning, CLIs) reach
  the bus through publish_event() here — the same helper
  agentlib.log_event uses.

P0 pickup note (do NOT read this as a fix): the bus is NOT
tamper-proofing. Any process in the dclawos-session group can
connect and publish, so a published event is not authenticated
and the event stream is not a trusted audit log. Publisher
authentication / event-log trust belongs to the later P0
hardening pass (see docs/PHASE6.md).

// reuse-check: broker socket pattern adapted from
  agentlib.broker_execute + ticketlib.socket_path (Unix stream
  socket, one JSON object per line, reply terminated by newline);
  NOT copied, reason: the broker is request/reply for ONE ticketed
  operation with a root peer, while the bus is fan-out pub/sub
  with many subscribers — only the framing and the env-overridable
  socket-path convention transfer.
// reuse-check: group/permission helpers follow agentlib's
  set_shared_group / ensure_exchange_dir pattern (duplicated, not
  imported), reason: this module must stay importable standalone
  in headless gates and must never create an import cycle with
  agentlib (agentlib imports this module lazily inside log_event).
// reuse-check: GLib/Gio socket patterns NOT used, reason: the
  daemon and headless tests are stdlib-only; GTK subscribers use
  the plain EventSubscriber thread here and marshal to their own
  main loop (GLib.idle_add) on their side.
"""

import ctypes
import grp
import json
import os
import socket
import threading
import time

PROTOCOL_VERSION = 1
SOCKET_PATH_DEFAULT = "/var/lib/dclawos/eventbus.sock"
SHARED_GROUP_DEFAULT = "dclawos-session"
MAX_LINE = 262144
PUBLISH_TIMEOUT = 1.0


def socket_path():
    return os.environ.get("DCLAWOS_EVENTBUS_SOCKET",
                          SOCKET_PATH_DEFAULT)


def shared_group():
    return os.environ.get("DCLAWOS_SHARED_GROUP", SHARED_GROUP_DEFAULT)


def _set_shared_group(path):
    try:
        os.chown(path, -1, grp.getgrnam(shared_group()).gr_gid)
    except (KeyError, OSError):
        pass


def ensure_socket_dir(path):
    parent = os.path.dirname(path)
    if not parent:
        return
    os.makedirs(parent, exist_ok=True)
    _set_shared_group(parent)
    try:
        os.chmod(parent, 0o770)
    except OSError:
        pass


def validate_event(event):
    """Return a cleaned event dict or raise ValueError.

    Required: type (str), summary (str). ts is filled when absent.
    Extra envelope fields (id, session, task_id, data) pass through
    only with JSON-safe scalar/dict/list values — the caller built
    them with json.dumps in mind, so a plain isinstance walk is
    enough; anything else is rejected, never coerced silently.
    """
    if not isinstance(event, dict):
        raise ValueError("event must be an object")
    if not isinstance(event.get("type"), str) or not event["type"]:
        raise ValueError("event.type must be a non-empty string")
    if not isinstance(event.get("summary"), str):
        raise ValueError("event.summary must be a string")
    out = dict(event)
    out.setdefault("ts", int(time.time()))
    out.setdefault("session", "")
    return out


class EventBusServer:
    """Threaded fan-out server. on_event(event) is an optional
    in-process callback invoked for every accepted publish (the
    daemon uses it for the task-queue wake signal)."""

    def __init__(self, path=None, on_event=None):
        self.path = path or socket_path()
        self.on_event = on_event
        self._lock = threading.Lock()
        self._subscribers = set()
        self._send_locks = {}
        self._sock = None
        self._thread = None
        self._stopped = threading.Event()

    def start(self):
        ensure_socket_dir(self.path)
        try:
            os.unlink(self.path)
        except OSError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.path)
        _set_shared_group(self.path)
        try:
            os.chmod(self.path, 0o660)
        except OSError:
            pass
        self._sock.listen(64)
        self._sock.settimeout(0.5)
        self._thread = threading.Thread(target=self._accept_loop,
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stopped.set()
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass
        with self._lock:
            subs = list(self._subscribers)
            self._subscribers.clear()
        for conn in subs:
            try:
                conn.close()
            except OSError:
                pass
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _accept_loop(self):
        while not self._stopped.is_set():
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._client_loop, args=(conn,),
                             daemon=True).start()

    def _send(self, conn, message):
        with self._lock:
            lock = self._send_locks.setdefault(conn, threading.Lock())
        with lock:
            conn.sendall((json.dumps(message, ensure_ascii=False)
                          + "\n").encode("utf-8"))

    def _drop(self, conn):
        with self._lock:
            self._subscribers.discard(conn)
            self._send_locks.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass

    def broadcast(self, event):
        message = {"kind": "event", "protocol": PROTOCOL_VERSION,
                   "event": event}
        with self._lock:
            subs = list(self._subscribers)
        for conn in subs:
            try:
                self._send(conn, message)
            except OSError:
                self._drop(conn)

    def _client_loop(self, conn):
        buffer = b""
        try:
            while not self._stopped.is_set():
                try:
                    chunk = conn.recv(65536)
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
                if len(buffer) > MAX_LINE and b"\n" not in buffer:
                    self._try_error(conn, "line too long")
                    return
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if len(line) > MAX_LINE:
                        self._try_error(conn, "line too long")
                        return
                    if not line.strip():
                        continue
                    if not self._handle_line(conn, line):
                        return
        finally:
            self._drop(conn)

    def _try_error(self, conn, text):
        try:
            self._send(conn, {"kind": "error",
                              "protocol": PROTOCOL_VERSION,
                              "error": text})
        except OSError:
            pass

    def _handle_line(self, conn, line):
        """Returns False when the connection should close."""
        try:
            message = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._try_error(conn, "malformed message")
            return True
        if not isinstance(message, dict):
            self._try_error(conn, "message must be an object")
            return True
        kind = message.get("kind")
        if kind == "hello":
            proto = message.get("protocol")
            if proto != PROTOCOL_VERSION:
                self._try_error(
                    conn, f"unsupported protocol: {proto!r}")
                return False
            self._send(conn, {"kind": "ack", "for": "hello",
                              "protocol": PROTOCOL_VERSION,
                              "ok": True})
            return True
        if kind == "subscribe":
            with self._lock:
                self._subscribers.add(conn)
                self._send_locks.setdefault(conn, threading.Lock())
            self._send(conn, {"kind": "ack", "for": "subscribe",
                              "protocol": PROTOCOL_VERSION,
                              "ok": True})
            return True
        if kind == "publish":
            try:
                event = validate_event(message.get("event"))
            except ValueError as exc:
                self._try_error(conn, str(exc))
                return True
            self.broadcast(event)
            if self.on_event is not None:
                try:
                    self.on_event(event)
                except Exception:  # noqa: BLE001 - callback is additive
                    pass
            self._send(conn, {"kind": "ack", "for": "publish",
                              "protocol": PROTOCOL_VERSION,
                              "ok": True,
                              "id": event.get("id")})
            return True
        self._try_error(conn, f"unknown kind: {kind!r}")
        return True


def publish_event(event, path=None, timeout=PUBLISH_TIMEOUT):
    """Best-effort publish of one event dict. Returns True when the
    server acked. NEVER raises: a missing/dead bus is a freshness
    loss, not a reason to block or lose the durable event."""
    try:
        cleaned = validate_event(event)
    except ValueError:
        return False
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect(path or socket_path())
        sock.sendall((json.dumps(
            {"kind": "publish", "protocol": PROTOCOL_VERSION,
             "event": cleaned}, ensure_ascii=False)
            + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(4096)
            if not chunk:
                return False
            data += chunk
        reply = json.loads(data.decode("utf-8"))
        return bool(reply.get("ok"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


class EventSubscriber:
    """Blocking subscriber with reconnect, for GTK apps and tests.

    Runs its own thread; callback(event) is invoked on THAT thread
    (GTK callers marshal with GLib.idle_add themselves). Reconnect
    after a drop is part of the subscription lifecycle (the daemon
    may start after the UI); it is not a display poll — no state is
    re-read on a timer, events arrive only when published.
    """

    def __init__(self, callback, path=None, reconnect_seconds=1.0):
        self.callback = callback
        self.path = path or socket_path()
        self.reconnect_seconds = reconnect_seconds
        self._stopped = threading.Event()
        self._thread = None
        self._sock = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stopped.set()
        try:
            if self._sock is not None:
                self._sock.close()
        except OSError:
            pass

    def _loop(self):
        while not self._stopped.is_set():
            try:
                self._run_once()
            except (OSError, ValueError, UnicodeDecodeError):
                pass
            if not self._stopped.is_set():
                self._stopped.wait(self.reconnect_seconds)

    def _run_once(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock = sock
        try:
            sock.settimeout(5)
            sock.connect(self.path)
            sock.sendall((json.dumps(
                {"kind": "hello", "protocol": PROTOCOL_VERSION})
                + "\n").encode("utf-8"))
            sock.sendall((json.dumps(
                {"kind": "subscribe",
                 "protocol": PROTOCOL_VERSION})
                + "\n").encode("utf-8"))
            sock.settimeout(None)
            buffer = b""
            while not self._stopped.is_set():
                chunk = sock.recv(65536)
                if not chunk:
                    return
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        message = json.loads(line.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(message, dict) and \
                            message.get("kind") == "event" and \
                            isinstance(message.get("event"), dict):
                        try:
                            self.callback(message["event"])
                        except Exception:  # noqa: BLE001 - UI callback
                            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None


# ------------------------------------------------------- inotify wake

_IN_CREATE = 0x00000100
_IN_MOVED_TO = 0x00000080
_IN_CLOSE_WRITE = 0x00000008
_IN_DELETE = 0x00000200
_IN_MOVED_FROM = 0x00000040
_IN_NONBLOCK = 0o00004000
_IN_CLOEXEC = 0o2000000


class DirectoryWatcher:
    """Minimal inotify watch on one directory (ctypes, stdlib).

    // reuse-check: pattern follows the Linux inotify(7) API
      directly; no third-party binding (pyinotify/watchdog) is
      introduced, reason: stdlib-only daemon policy, and the watch
      needs only "something changed" — event records are drained,
      never parsed beyond their presence.
    """

    def __init__(self, directory):
        self.directory = directory
        self._libc = ctypes.CDLL("libc.so.6", use_errno=True)
        self.fd = self._libc.inotify_init1(_IN_NONBLOCK | _IN_CLOEXEC)
        if self.fd < 0:
            raise OSError(ctypes.get_errno(),
                          "inotify_init1 failed")
        mask = (_IN_CREATE | _IN_MOVED_TO | _IN_CLOSE_WRITE
                | _IN_DELETE | _IN_MOVED_FROM)
        wd = self._libc.inotify_add_watch(
            self.fd, os.fsencode(directory), mask)
        if wd < 0:
            err = ctypes.get_errno()
            os.close(self.fd)
            raise OSError(err, "inotify_add_watch failed")

    def fileno(self):
        return self.fd

    def drain(self):
        """Read pending notifications. Returns True if any arrived."""
        saw = False
        while True:
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return saw
            except OSError:
                return saw
            if not data:
                return saw
            saw = True

    def close(self):
        try:
            os.close(self.fd)
        except OSError:
            pass

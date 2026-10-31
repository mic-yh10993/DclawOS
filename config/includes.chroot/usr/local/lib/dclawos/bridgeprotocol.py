#!/usr/bin/env python3
"""Host-bridge protocol core (Phase 6 module M1). Pure Python.

Foundation for the later WebView modules (M2-M4): local HTML pages
run in a WebView hosted by a DclawOS app; the page talks to its
host ONLY through the typed JSON envelopes defined here (the full
specification is docs/BRIDGE-PROTOCOL.md). This module is the
shared validator / allowlist core a host imports; it builds NO
page and NO WebView.

Hard rules (non-negotiable, enforced here where code can):
- Page content is UNTRUSTED. The host never evals page-supplied
  code, never treats a page string as a path/command/template,
  and dispatches ONLY allowlisted verbs to typed handlers.
- Every command carries a request id; every reply echoes it.
- Secrets are WRITE-ONLY across the bridge: a page may submit a
  secret value (e.g. a provider key form) only inside a command
  whose allowlist entry is marked secret-bearing; replies and
  events must NEVER contain secret values — make_reply /
  make_event strip secret-looking keys recursively, and
  validate_command returns a redacted copy for logging.
- Version negotiation happens in hello: no hello, no commands.

// reuse-check: envelope shape (id + verb + params, reply echoes
  id) adapted from mcpclient's JSON-RPC request/reply discipline;
  NOT copied, reason: MCP is a tool protocol between the agent and
  external servers, while the bridge is a UI trust boundary — the
  allowlist, secret rules and surface scoping here have no MCP
  counterpart and are DclawOS-written.
// reuse-check: newline-JSON socket framing NOT reused here on
  purpose, reason: the bridge transport is the WebView host
  message channel (M2+), not a socket; this core is transport-free
  so any host channel can carry the same envelopes.
"""

PROTOCOL_NAME = "dclawos-bridge"
PROTOCOL_VERSION = 1

SECRET_KEYS = frozenset({
    "secret", "token", "password", "passwd", "api_key", "apikey",
    "auth", "authorization", "credential", "credentials",
    "value_secret",
})

# Per-surface command allowlists. A verb not listed for the
# surface is refused, even if another surface allows it. Entries:
# verb -> {"secret_params": bool} (True = params may carry secret
# values, write-only; the host handler must never echo them).
SURFACE_COMMANDS = {
    # Generic host services every surface may use.
    "host": {
        "hello": {"secret_params": False},
        "ping": {"secret_params": False},
    },
    # M2 (settings WebView): config reads/writes via settingslib /
    # registrylib on the host side; secret.set is the write-only
    # broker write-secret path (value never comes back).
    # Phase 6 M2 adds the model.* verbs (docs/BRIDGE-PROTOCOL.md):
    # catalog/override merge reads, custom-provider CRUD, builtin
    # override save/reset, routing+pin config, health, connectivity
    # test and usage aggregation. None of them is secret-bearing:
    # provider keys cross ONLY via secret.set, exactly as before.
    "settings": {
        "settings.get": {"secret_params": False},
        "settings.set": {"secret_params": False},
        "secret.set": {"secret_params": True},
        "registry.list": {"secret_params": False},
        "model.catalog": {"secret_params": False},
        "model.provider.save": {"secret_params": False},
        "model.provider.delete": {"secret_params": False},
        "model.provider.override": {"secret_params": False},
        "model.provider.reset": {"secret_params": False},
        "model.config.get": {"secret_params": False},
        "model.config.save": {"secret_params": False},
        "model.health": {"secret_params": False},
        "model.test": {"secret_params": False},
        "model.usage": {"secret_params": False},
    },
    # M3 (chat WebView): full chat surface (docs/BRIDGE-PROTOCOL.md
    # M3 table). Replies arrive as pushed events; commands cover
    # session management, the unified send/assign entry points,
    # the model switcher, inline approval verdicts (ticket_id
    # only — the host writes the verdict onto the ticket file
    # exactly as dclawos-approve does), stop, copy and launching
    # the sibling apps. None is secret-bearing: approval params
    # shown to the page are redacted host-side before they cross.
    "chat": {
        "chat.state": {"secret_params": False},
        "chat.send": {"secret_params": False},
        "chat.history": {"secret_params": False},
        "chat.assign": {"secret_params": False},
        "chat.switch": {"secret_params": False},
        "chat.new": {"secret_params": False},
        "chat.model.set": {"secret_params": False},
        "chat.approve": {"secret_params": False},
        "chat.deny": {"secret_params": False},
        "chat.stop": {"secret_params": False},
        "chat.copy": {"secret_params": False},
        "chat.launch": {"secret_params": False},
    },
    # M4 (dashboard WebView): read a snapshot, open the takeover
    # popup on the host side, acknowledge an event row.
    "dashboard": {
        "dashboard.snapshot": {"secret_params": False},
        "takeover.open": {"secret_params": False},
        "event.ack": {"secret_params": False},
    },
}

MAX_ID_LEN = 128


class BridgeError(ValueError):
    """Validation failure carrying the wire error shape."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

    def to_error(self, request_id=None):
        return make_error(request_id, self.code, self.message)


def make_error(request_id, code, message):
    return {
        "kind": "error",
        "protocol": PROTOCOL_VERSION,
        "id": request_id,
        "error": {"code": str(code), "message": str(message)[:500]},
    }


def _is_secret_key(key):
    return isinstance(key, str) and key.lower() in SECRET_KEYS


def strip_secrets(value):
    """Recursively drop secret-looking keys from reply/event data.
    Returns a new structure; the input is never mutated."""
    if isinstance(value, dict):
        return {k: strip_secrets(v) for k, v in value.items()
                if not _is_secret_key(k)}
    if isinstance(value, list):
        return [strip_secrets(item) for item in value]
    return value


def redact_params(params):
    """Copy of command params safe for logging: secret values are
    replaced, structure is kept."""
    if isinstance(params, dict):
        return {k: ("<redacted>" if _is_secret_key(k)
                    else redact_params(v))
                for k, v in params.items()}
    if isinstance(params, list):
        return [redact_params(item) for item in params]
    return params


def validate_hello(message):
    """Validate a page hello; returns the negotiated version.

    The page proposes {"kind":"hello","protocol":N,
    "protocol_name":"dclawos-bridge","surface":<surface>}. Only
    protocol 1 exists; anything else is refused, never downgraded
    silently.
    """
    if not isinstance(message, dict) or message.get("kind") != "hello":
        raise BridgeError("bad_hello", "expected a hello message")
    if message.get("protocol_name", PROTOCOL_NAME) != PROTOCOL_NAME:
        raise BridgeError("bad_hello", "unknown protocol_name")
    if message.get("protocol") != PROTOCOL_VERSION:
        raise BridgeError(
            "unsupported_protocol",
            f"bridge protocol {message.get('protocol')!r} "
            f"unsupported; host speaks {PROTOCOL_VERSION}")
    surface = message.get("surface", "host")
    if surface not in SURFACE_COMMANDS:
        raise BridgeError("unknown_surface",
                          f"unknown surface: {surface!r}")
    return PROTOCOL_VERSION


def make_hello_ack(surface):
    return {
        "kind": "hello_ack",
        "protocol": PROTOCOL_VERSION,
        "protocol_name": PROTOCOL_NAME,
        "surface": surface,
        "commands": sorted(SURFACE_COMMANDS.get(surface, {})),
    }


def validate_command(message, surface):
    """Validate one page->host command envelope for `surface`.

    Returns a normalised dict {id, surface, command, params}.
    Raises BridgeError for anything malformed or not allowlisted.
    Callers log redact_params(normalised["params"]), never the raw
    params of a secret-bearing command.
    """
    if not isinstance(message, dict):
        raise BridgeError("bad_message", "command must be an object")
    if message.get("kind") != "command":
        raise BridgeError("bad_kind", "expected kind='command'")
    if message.get("protocol", PROTOCOL_VERSION) != PROTOCOL_VERSION:
        raise BridgeError("unsupported_protocol",
                          "command protocol not supported")
    request_id = message.get("id")
    if not isinstance(request_id, str) or not request_id \
            or len(request_id) > MAX_ID_LEN:
        raise BridgeError("bad_id", "command needs a string id")
    if surface not in SURFACE_COMMANDS:
        raise BridgeError("unknown_surface",
                          f"unknown surface: {surface!r}")
    command = message.get("command")
    allowed = SURFACE_COMMANDS[surface]
    # The generic host verbs are available on every surface.
    merged = dict(SURFACE_COMMANDS["host"])
    merged.update(allowed)
    if not isinstance(command, str) or command not in merged:
        raise BridgeError(
            "not_allowlisted",
            f"command {command!r} is not allowlisted for "
            f"surface {surface!r}")
    params = message.get("params", {})
    if not isinstance(params, dict):
        raise BridgeError("bad_params", "params must be an object")
    if not merged[command]["secret_params"]:
        leaked = [k for k in params if _is_secret_key(k)]
        if leaked:
            raise BridgeError(
                "secret_not_allowed",
                f"command {command!r} may not carry secret params: "
                f"{sorted(leaked)}")
    return {"id": request_id, "surface": surface,
            "command": command, "params": params}


def make_reply(request_id, result):
    """Host->page success reply. Result is secret-stripped here so
    a handler cannot echo a secret by accident."""
    return {
        "kind": "reply",
        "protocol": PROTOCOL_VERSION,
        "id": request_id,
        "ok": True,
        "result": strip_secrets(result if result is not None else {}),
    }


def make_event(event_type, data):
    """Host->page pushed event (e.g. a forwarded bus event).
    Data is secret-stripped for the same reason as replies."""
    if not isinstance(event_type, str) or not event_type:
        raise BridgeError("bad_event", "event type must be a string")
    return {
        "kind": "event",
        "protocol": PROTOCOL_VERSION,
        "type": event_type,
        "data": strip_secrets(data if data is not None else {}),
    }

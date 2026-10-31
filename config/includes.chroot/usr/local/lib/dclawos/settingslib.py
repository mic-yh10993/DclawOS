#!/usr/bin/env python3
"""Shared settings store (Phase 5 follow-up module P5-M7).

User decision 2026-10-31 (chat), option B verbatim: "设置改存双方
共读的配置文件" — settings move to a config file both sides read.

Why this module exists: GSettings is per-user dconf. The settings
GUI writes as the human user (dclaw); the agent daemon runs as
dclaw-agent and its `gsettings get` read ITS OWN dconf database —
always the schema defaults. The Phase 3 full-access toggle and the
AI provider/model keys the GUI wrote were therefore invisible to
the daemon (a silent no-op, the exact "fake implementation" the
Phase 5 WS2 audit flagged for settings_set). The fix is one store
both sides share:

File: /var/lib/dclawos/agent-settings.json (env override
DCLAWOS_SETTINGS_FILE, the same override the engine's chaos gate
already honours), group dclawos-session, mode 0660 — the same
sharing pattern as agent-registry.json (registrylib) and the task
exchange (agentlib). /var/lib/dclawos itself is 0770
dclaw:dclawos-session (hook 1005), so only the human user and
dclaw-agent can reach the file.

Precedent (step 0 retrieval): the file path and the chaos-enabled
key predate this module — the routing engine's qualityStore.ts
chaosEnabled() has read /var/lib/dclawos/agent-settings.json since
Phase 4; nobody wrote it. This library adopts that path and key,
so the chaos gate migrates with zero engine changes.

Contract:
- KEY_SPECS is the single key inventory: the seven keys of the
  org.dclawos.settings GSettings schema plus chaos-enabled.
  Unknown keys are rejected on write; unknown keys found in the
  file are ignored on read (forward compatibility).
- Values are type-checked (bool is not int), enums enforced,
  strings length-capped. A file value of the wrong type falls
  back to that key's default; a wholly corrupt file falls back
  to all defaults and is logged — readers NEVER crash, and the
  next write renames the corrupt file to <path>.corrupt once
  (best effort) before writing a clean one.
- Writes are per-key read-modify-write under an flock on
  <path>.lock, then atomic (tmp + rename, 0660, shared group):
  the GUI and the daemon cannot tear each other's updates, and
  neither side blind-overwrites the whole file.
- AGENT_DENIED_KEYS is the hard security boundary for the
  settings_set tool: keys the agent may never set, at any tier,
  even with full access on — it governs the agent's own
  permission grant and its own fault-injection switch. (The
  approval tiers themselves are NOT settings: they are hardcoded
  in agentlib.classify, so there is no tier-policy key to deny.)
  Tokens/secrets are never settings: they stay in the broker's
  0600 secret files by design and must never be added here.

GSettings after the migration: the schema stays compiled (hook
1002) ONLY as the first-run seed source — when the shared file
is absent, the settings GUI seeds it from the human's current
GSettings values (seed()), after which the file is authoritative
and GSettings is no longer read or written by any DclawOS app.
// reuse-check: agentlib.atomic_write / registrylib._atomic_write
  pattern adopted (tmp+rename, group chown, 0660) rather than
  imported, reason: registrylib already duplicates the same ~10
  lines for the same reason — these libs must stay importable
  standalone (no local imports) in headless gates.
"""

import fcntl
import grp
import json
import os
import sys
import time

SETTINGS_DEFAULT = "/var/lib/dclawos/agent-settings.json"
SHARED_GROUP_DEFAULT = "dclawos-session"
MAX_STRING = 512

# key -> (kind, default, choices-or-None)
# kind: "bool" | "string" | "enum"
KEY_SPECS = {
    "window-mode": ("enum", "fullscreen", ("fullscreen", "windowed")),
    "taskbar-autohide": ("bool", False, None),
    "ai-provider": ("string", "", None),
    "ai-model": ("string", "", None),
    "routing-mode": ("enum", "auto", ("auto", "manual")),
    "ui-dark": ("bool", False, None),
    "agent-full-access": ("bool", False, None),
    "chaos-enabled": ("bool", False, None),
}

DEFAULTS = {key: spec[1] for key, spec in KEY_SPECS.items()}

# Human-only keys: the agent's settings_set must refuse these even
# at tier 2 and even in full-access mode (Phase 5 human-only list:
# G-13 full-access toggle; chaos-enabled governs fault injection
# into the agent's own dispatch — a human testing/oversight
# switch, not something the agent enables on itself).
AGENT_DENIED_KEYS = frozenset({"agent-full-access", "chaos-enabled"})


def settings_path():
    return os.environ.get("DCLAWOS_SETTINGS_FILE", SETTINGS_DEFAULT)


def _shared_group():
    return os.environ.get("DCLAWOS_SHARED_GROUP", SHARED_GROUP_DEFAULT)


def _log_warning(message):
    print(f"settingslib: {message}", file=sys.stderr, flush=True)


def _set_shared_group(path):
    try:
        os.chown(path, -1, grp.getgrnam(_shared_group()).gr_gid)
    except (KeyError, OSError):
        pass


def _atomic_write(path, text):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    _set_shared_group(tmp)
    try:
        os.chmod(tmp, 0o660)
    except OSError:
        pass
    os.replace(tmp, path)


class _FileLock:
    """flock on <path>.lock; degrades to no-lock on any error
    (atomic rename still guarantees readers never see torn data).
    """

    def __init__(self, path):
        self._lock_path = path + ".lock"
        self._handle = None

    def __enter__(self):
        try:
            fd = os.open(self._lock_path,
                         os.O_CREAT | os.O_RDWR, 0o660)
            self._handle = os.fdopen(fd, "r+")
            _set_shared_group(self._lock_path)
            deadline = time.time() + 10
            while True:
                try:
                    fcntl.flock(self._handle, fcntl.LOCK_EX |
                                fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() > deadline:
                        raise
                    time.sleep(0.05)
        except OSError:
            if self._handle is not None:
                self._handle.close()
            self._handle = None
        return self

    def __exit__(self, *exc):
        if self._handle is not None:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_UN)
            except OSError:
                pass
            self._handle.close()
            self._handle = None
        return False


def validate_value(key, value):
    """Return the value if it fits KEY_SPECS, else raise ValueError."""
    spec = KEY_SPECS.get(key)
    if spec is None:
        raise ValueError(f"unknown settings key: {key!r}")
    kind, _default, choices = spec
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean, got "
                             f"{type(value).__name__}")
        return value
    if kind == "enum":
        if not isinstance(value, str) or value not in choices:
            raise ValueError(f"{key} must be one of "
                             f"{sorted(choices)}, got {value!r}")
        return value
    # string
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string, got "
                         f"{type(value).__name__}")
    if len(value) > MAX_STRING:
        raise ValueError(f"{key} is too long (>{MAX_STRING} chars)")
    return value


def _read_raw():
    """Return (stored_dict_or_None, corrupt_bool). Missing file is
    (None, False): defaults apply and nothing is corrupt."""
    path = settings_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, False
    except (OSError, ValueError) as exc:
        _log_warning(f"{path} unreadable ({exc}); using defaults")
        return None, True
    if not isinstance(data, dict):
        _log_warning(f"{path} is not a JSON object; using defaults")
        return None, True
    return data, False


def load_settings():
    """All keys with defaults filled. Never raises: a bad stored
    value falls back to its key's default (logged), a corrupt file
    to all defaults."""
    stored, _corrupt = _read_raw()
    values = dict(DEFAULTS)
    if stored:
        for key, value in stored.items():
            if key not in KEY_SPECS:
                continue
            try:
                values[key] = validate_value(key, value)
            except ValueError as exc:
                _log_warning(f"stored value rejected ({exc}); "
                             f"default applies")
    return values


def get_setting(key):
    if key not in KEY_SPECS:
        raise ValueError(f"unknown settings key: {key!r}")
    return load_settings()[key]


def agent_denied(key):
    return key in AGENT_DENIED_KEYS


def file_exists():
    return os.path.exists(settings_path())


def _write_merged_locked(key, value):
    """Read-modify-write ONE key. Caller holds the lock. Returns
    the previous effective value."""
    stored, corrupt = _read_raw()
    if corrupt:
        try:
            os.replace(settings_path(), settings_path() + ".corrupt")
            _log_warning("corrupt settings file set aside as "
                         f"{settings_path()}.corrupt")
        except OSError:
            pass
        stored = None
    current = dict(DEFAULTS)
    if stored:
        for known, raw in stored.items():
            if known not in KEY_SPECS:
                continue
            try:
                current[known] = validate_value(known, raw)
            except ValueError:
                pass
    old = current[key]
    current[key] = value
    path = settings_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _atomic_write(path, json.dumps(current, indent=2, sort_keys=True)
                  + "\n")
    return old


def set_setting(key, value, agent=False):
    """Validate and persist one key. Returns the previous value.

    agent=True enforces AGENT_DENIED_KEYS (fail closed) — the
    daemon's settings_set passes agent=True; the human GUI does
    not. Raises ValueError on unknown key / bad value / denied key.
    """
    value = validate_value(key, value)
    if agent and agent_denied(key):
        raise ValueError(
            f"{key} is human-only: the agent may never set it "
            f"(denied even with full access)")
    with _FileLock(settings_path()):
        return _write_merged_locked(key, value)


def seed(seed_values):
    """Create the shared file from seed_values (validated, merged
    over defaults) ONLY if it does not exist yet. Returns True if
    the file was created. Used by the settings GUI's first-run
    GSettings migration; safe against a concurrent creator."""
    clean = {}
    for key, value in (seed_values or {}).items():
        if key not in KEY_SPECS:
            continue
        try:
            clean[key] = validate_value(key, value)
        except ValueError:
            continue
    with _FileLock(settings_path()):
        if file_exists():
            return False
        merged = dict(DEFAULTS)
        merged.update(clean)
        path = settings_path()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _atomic_write(path, json.dumps(merged, indent=2,
                                       sort_keys=True) + "\n")
        return True

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

File: /var/lib/dclawos/settings/agent-settings.json (env override
DCLAWOS_SETTINGS_FILE, the same override the engine's chaos gate
already honours).

WRITE-PATH HARDENING (external security review 2026-10-31, P0
item 2 — the review is right, and this is the fix): the first cut
made the file group-writable (0660) and relied on the
AGENT_DENIED_KEYS check in this library — worthless against the
agent's own shell tool, which could edit the JSON directly and
set agent-full-access=true. Now:
- The file is owned by the human user (dclaw), mode 0640, group
  dclawos-session: the agent can READ it, never write it.
- It lives in its own subdirectory (dclaw:dclawos-session, 0710:
  group may traverse, not list/create/unlink). The parent
  /var/lib/dclawos is group-writable by design (tasks/events
  exchange), so a file placed directly inside could be unlinked
  and replaced by the agent despite 0640 — the subdirectory
  closes that rename path. Hook 1005 creates the directory.
- The GUI writes directly (it runs as the owner).
- The AGENT never writes the file: settings_set goes through the
  privileged broker's set-setting operation (root code), which
  enforces the denylist in trusted code, validates, performs the
  write via apply_setting() below and logs the event. The
  denylist in this library remains as defense in depth only —
  the enforcement points are the file permissions + the broker.
- Every write re-chowns the file to the canonical owner (dclaw)
  before the atomic rename, so a root (broker) write never
  locks the human out of their own settings file.

Precedent (step 0 retrieval): the settings file name and the
chaos-enabled key predate this module — the routing engine's
qualityStore.ts chaosEnabled() has read an agent-settings.json
under DCLAWOS_SETTINGS_FILE since Phase 4; nobody wrote it. This
library adopts that file (moved into the settings/ subdirectory
for the hardening above; the engine default path moved with it).

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
  <path>.lock, then atomic (tmp + rename, 0640, canonical owner):
  the GUI and the daemon cannot tear each other's updates, and
  neither side blind-overwrites the whole file.
- AGENT_DENIED_KEYS lists the keys the agent may never set, at
  any tier, even with full access on — its own permission grant
  and its own fault-injection switch. ENFORCEMENT lives in the
  broker's set-setting op (root) + the file permissions above;
  the copy here is defense in depth and the shared definition
  the broker imports. (The approval tiers themselves are NOT
  settings: they are hardcoded in agentlib.classify, so there is
  no tier-policy key to deny.) Tokens/secrets are never settings:
  they stay in the broker's 0600 secret files by design and must
  never be added here.

GSettings after the migration: the schema stays compiled (hook
1002) ONLY as the first-run seed source — when the shared file
is absent, the settings GUI seeds it from the human's current
GSettings values (seed()), after which the file is authoritative
and GSettings is no longer read or written by any DclawOS app.
// reuse-check: agentlib.atomic_write / registrylib._atomic_write
  pattern adopted (tmp+rename, owner/group chown, mode) rather than
  imported, reason: registrylib already duplicates the same ~10
  lines for the same reason — these libs must stay importable
  standalone (no local imports) in headless gates.
"""

import fcntl
import grp
import json
import os
import pwd
import sys
import time

SETTINGS_DEFAULT = "/var/lib/dclawos/settings/agent-settings.json"
SHARED_GROUP_DEFAULT = "dclawos-session"
SETTINGS_OWNER_DEFAULT = "dclaw"
FILE_MODE = 0o640
DIR_MODE = 0o710
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


def _set_canonical_owner(path):
    """Chown to the settings owner (dclaw) + shared group, best
    effort: a root (broker) write must leave the file owned by the
    human, or the GUI could no longer write its own settings. A
    non-root writer can only chown to itself/groups it belongs to,
    which is exactly the same target."""
    try:
        uid = pwd.getpwnam(os.environ.get(
            "DCLAWOS_SETTINGS_OWNER", SETTINGS_OWNER_DEFAULT)).pw_uid
    except KeyError:
        uid = -1
    try:
        gid = grp.getgrnam(_shared_group()).gr_gid
    except KeyError:
        gid = -1
    if uid == -1 and gid == -1:
        return
    try:
        os.chown(path, uid, gid)
    except OSError:
        pass


def _atomic_write(path, text):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
    _set_canonical_owner(tmp)
    try:
        os.chmod(tmp, FILE_MODE)
    except OSError:
        pass
    os.replace(tmp, path)


def _ensure_parent():
    """Create the settings directory if needed and restamp its
    hardened mode/group (hook 1005 creates it at build time; this
    is the runtime safety net, owner/root only in effect)."""
    parent = os.path.dirname(settings_path())
    if not parent:
        return
    os.makedirs(parent, exist_ok=True)
    _set_canonical_owner(parent)
    try:
        os.chmod(parent, DIR_MODE)
    except OSError:
        pass


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
    _ensure_parent()
    _atomic_write(settings_path(),
                  json.dumps(current, indent=2, sort_keys=True) + "\n")
    return old


def apply_setting(key, value):
    """Validate and persist one key (locked read-modify-write).
    Returns the previous value. This is the DIRECT write path: the
    human GUI (file owner) and the root broker's set-setting op
    call it. The broker performs its own denylist check first;
    this function deliberately does not re-check it (root code is
    the enforcement point and the GUI must be able to set the
    human-only keys). Raises ValueError on unknown key/bad value.
    """
    value = validate_value(key, value)
    with _FileLock(settings_path()):
        return _write_merged_locked(key, value)


def set_setting(key, value, agent=False):
    """Human-path wrapper over apply_setting.

    agent=True is NOT a write path: since the P0-2 hardening the
    agent's settings_set is executed by the root broker, so an
    agent-side caller reaching this function is refused — denied
    keys with the denylist message (defense in depth), other keys
    with a pointer to the broker op. Raises ValueError.
    """
    value = validate_value(key, value)
    if agent:
        if agent_denied(key):
            raise ValueError(
                f"{key} is human-only: the agent may never set it "
                f"(denied even with full access)")
        raise ValueError(
            "agent settings writes must go through the privileged "
            "broker 'set-setting' operation, not the shared library")
    return apply_setting(key, value)


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
        _ensure_parent()
        _atomic_write(settings_path(),
                      json.dumps(merged, indent=2, sort_keys=True)
                      + "\n")
        return True

#!/usr/bin/env python3
"""Shared settings store selftest (Phase 5 follow-up P5-M7, build gate).

User decision 2026-10-31 option B: settings live in ONE file both
sides read (/var/lib/dclawos/settings/agent-settings.json via
settingslib). Write path hardened per external review P0 item 2:
the file is human-owned 0640 in a 0710 directory (agent read-only,
cannot create/unlink/replace), and the agent's settings_set is
executed by the root broker's set-setting op, which enforces the
human-only denylist in trusted code.

Exercised here, against temp files:

- tool wiring alignment (toolslib schemas == agentlib.TOOLS) and
  classify() tiers: settings_get 0, settings_set 2;
- defaults on a missing file; one-time seed semantics (invalid
  seed entries skipped, second seed refused); file mode 0640;
- round trip through the lib (one lib, both sides);
- lib-level denylist (defense in depth): set_setting(agent=True)
  refused for denied keys AND routed to the broker for the rest;
  the human path works, agentlib.full_access_enabled() follows;
- the broker op (dclawos-privileged op_set_setting, in-process —
  the socket/ticket layer is the pre-existing machinery shared by
  all ops and cannot run in a build chroot): allowed key applied,
  agent-full-access REFUSED by root code, bad value refused, the
  change event logged;
- daemon execute_step denylist pre-check fires BEFORE any ticket;
- validation: bad enum / bad type / unknown key / over-long string
  refused; a wrong-typed stored value falls back to its default;
- corrupt file: load returns defaults (daemon would still start),
  the next write sets the corrupt file aside as <path>.corrupt;
- THE ATTACK, two-user simulation (required in the image gate):
  as dclaw-agent, a direct lib write to the real settings
  directory FAILS (including the agent-full-access self-grant),
  reads succeed; as dclaw, writes succeed and the file stays
  dclaw-owned 0640 even after a root broker write; nobody cannot
  open the file at all.

Exits non-zero on the first failed assertion. Invoked by build
hook 1008 inside the image chroot; also runnable by hand (it only
touches a temp dir, plus one sim file it removes afterwards).
"""

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, "/usr/local/lib/dclawos")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = tempfile.mkdtemp(prefix="dclawos-settingstest-")
SETTINGS_FILE = os.path.join(ROOT, "agent-settings.json")
os.environ["DCLAWOS_SETTINGS_FILE"] = SETTINGS_FILE
os.environ["DCLAWOS_EVENTS_PATH"] = os.path.join(ROOT, "events.jsonl")
os.environ["DCLAWOS_TASKS_DIR"] = os.path.join(ROOT, "tasks")

import agentlib  # noqa: E402
import settingslib  # noqa: E402
import toolslib  # noqa: E402


def fail(message):
    print(f"settingstest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def expect_error(fn, needle):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - assertion helper
        if needle and needle not in str(exc):
            fail(f"error {exc!r} does not mention {needle!r}")
        return
    fail(f"expected an error mentioning {needle!r}, got success")


# ------------------------------------------------------- tool wiring
if set(toolslib.SCHEMA_NAMES) != set(agentlib.TOOLS):
    fail(f"tool schema/dispatch drift: "
         f"{set(toolslib.SCHEMA_NAMES) ^ set(agentlib.TOOLS)}")
for tool, params, wanted in (
        ("settings_get", {}, 0),
        ("settings_get", {"key": "ui-dark"}, 0),
        ("settings_set", {"key": "ui-dark", "value": True}, 2),
        ("settings_set", {"key": "agent-full-access", "value": True}, 2)):
    tier, _reason = agentlib.classify(tool, params)
    if tier != wanted:
        fail(f"classify({tool}) = tier {tier}, want {wanted}")

# ---------------------------------------------------------- defaults
if settingslib.file_exists():
    fail("settings file should not exist yet")
if settingslib.load_settings() != settingslib.DEFAULTS:
    fail("missing file must yield pure defaults")
if agentlib.full_access_enabled() is not False:
    fail("full access must default to False")

# -------------------------------------------------------------- seed
created = settingslib.seed({
    "ai-provider": "openai",
    "agent-full-access": True,
    "window-mode": "not-a-mode",      # invalid: must be skipped
    "totally-unknown": 1,             # unknown: must be skipped
})
if not created:
    fail("first seed must create the file")
values = settingslib.load_settings()
if values["ai-provider"] != "openai":
    fail("seed ai-provider not stored")
if values["agent-full-access"] is not True:
    fail("seed agent-full-access not stored")
if values["window-mode"] != "fullscreen":
    fail("invalid seed value must fall back to the default")
if settingslib.seed({"ai-provider": "other"}):
    fail("second seed must be refused (file exists)")
if settingslib.get_setting("ai-provider") != "openai":
    fail("refused seed must not change values")
mode = stat.S_IMODE(os.stat(SETTINGS_FILE).st_mode)
if mode != 0o640:
    fail(f"settings file mode {oct(mode)}, want 0o640 "
         f"(human-owned, agent read-only — review P0 item 2)")

# ------------------------------------------------- cross-side round trip
old = settingslib.set_setting("ui-dark", True)  # daemon-side writer
if old is not False:
    fail(f"set_setting returned old={old!r}, want False")
if settingslib.get_setting("ui-dark") is not True:  # GUI-side reader
    fail("round trip failed: ui-dark not visible after write")
settingslib.set_setting("window-mode", "windowed")
values = settingslib.load_settings()
if values["window-mode"] != "windowed" or values["ui-dark"] is not True:
    fail("per-key read-modify-write lost a sibling key")

# ------------------------------------------------------------ denylist
expect_error(
    lambda: settingslib.set_setting("agent-full-access", False,
                                    agent=True),
    "human-only")
if settingslib.get_setting("agent-full-access") is not True:
    fail("denied write must not change agent-full-access")
expect_error(
    lambda: settingslib.set_setting("chaos-enabled", True, agent=True),
    "human-only")
if settingslib.get_setting("chaos-enabled") is not False:
    fail("denied write must not change chaos-enabled")
# The lib is not the agent's write path at all any more: even an
# allowed key via agent=True is refused and routed to the broker.
expect_error(
    lambda: settingslib.set_setting("ui-dark", False, agent=True),
    "broker")
if settingslib.get_setting("ui-dark") is not True:
    fail("agent=True lib write must not change anything "
         "(ui-dark was set True by the round trip above)")
# The human path owns the key, and the daemon's gate follows it.
settingslib.set_setting("agent-full-access", False)
if agentlib.full_access_enabled() is not False:
    fail("full_access_enabled must follow the shared file (off)")
settingslib.set_setting("agent-full-access", True)
if agentlib.full_access_enabled() is not True:
    fail("full_access_enabled must follow the shared file (on)")
settingslib.set_setting("agent-full-access", False)

# ----------------------------------------------------------- validation
expect_error(lambda: settingslib.set_setting("window-mode", "bogus"),
             "window-mode")
expect_error(lambda: settingslib.set_setting("ui-dark", "yes"),
             "boolean")
expect_error(lambda: settingslib.set_setting("no-such-key", 1),
             "unknown")
expect_error(lambda: settingslib.set_setting("ai-provider", "x" * 600),
             "too long")
expect_error(lambda: settingslib.get_setting("no-such-key"), "unknown")

# ------------------------------------------- wrong-typed stored values
with open(SETTINGS_FILE, "w", encoding="utf-8") as handle:
    json.dump({"ui-dark": "yes", "ai-model": 42,
               "routing-mode": "manual"}, handle)
values = settingslib.load_settings()
if values["ui-dark"] is not False or values["ai-model"] != "":
    fail("wrong-typed stored values must fall back to defaults")
if values["routing-mode"] != "manual":
    fail("valid stored value must survive sibling fallbacks")

# ------------------------------------------------------- corrupt file
with open(SETTINGS_FILE, "wb") as handle:
    handle.write(b"{not json at all")
values = settingslib.load_settings()
if values != settingslib.DEFAULTS:
    fail("corrupt file must yield pure defaults, not a crash")
if agentlib.full_access_enabled() is not False:
    fail("corrupt file must leave full access OFF")
settingslib.set_setting("ui-dark", True)
if not os.path.exists(SETTINGS_FILE + ".corrupt"):
    fail("write after corruption must set the corrupt file aside")
if settingslib.get_setting("ui-dark") is not True:
    fail("write after corruption must succeed cleanly")

# ------------------------------------------------- daemon dispatch fns
agent_path = "/usr/local/bin/dclawos-agent"
if not os.path.exists(agent_path):
    agent_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "bin", "dclawos-agent")
import importlib.machinery  # noqa: E402
_loader = importlib.machinery.SourceFileLoader("dclawos_agent",
                                               agent_path)
spec = importlib.util.spec_from_loader("dclawos_agent", _loader)
daemon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(daemon)

got = daemon._settings_get({})
if got["settings"]["ui-dark"] is not True:
    fail("daemon settings_get(all) mismatch")
if "agent-full-access" not in got["agent_denied_keys"]:
    fail("settings_get must report the denylist")
got = daemon._settings_get({"key": "ai-provider"})
# The corrupt-file phase above reset the store to defaults, so
# the seeded "openai" is gone — "" is the honest expectation here.
if got != {"key": "ai-provider", "value": ""}:
    fail(f"daemon settings_get(key) mismatch: {got!r}")
# The denylist pre-check in execute_step fires BEFORE any ticket
# or broker contact: a denied settings_set dies right there.
expect_error(
    lambda: daemon.execute_step(
        {"session": "settingstest"},
        {"tool": "settings_set",
         "params": {"key": "agent-full-access", "value": True}}),
    "human-only")

# ------------------------------------------------------ broker op
# The agent's real write path: the root broker's set-setting op,
# unit-tested in-process (the socket/ticket layer is shared
# pre-existing machinery and cannot run inside a build chroot).
broker_path = "/usr/local/bin/dclawos-privileged"
if not os.path.exists(broker_path):
    broker_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "bin", "dclawos-privileged")
_bloader = importlib.machinery.SourceFileLoader("dclawos_privileged",
                                                broker_path)
_bspec = importlib.util.spec_from_loader("dclawos_privileged",
                                         _bloader)
broker = importlib.util.module_from_spec(_bspec)
_bspec.loader.exec_module(broker)

ok, result = broker.op_set_setting({"key": "ai-model",
                                    "value": "gpt-x"})
if not ok or result["old"] != "" or result["new"] != "gpt-x":
    fail(f"broker set-setting (allowed key) mismatch: {ok} {result!r}")
if settingslib.get_setting("ai-model") != "gpt-x":
    fail("broker write not visible through the lib")
ok, error = broker.op_set_setting({"key": "agent-full-access",
                                   "value": True})
if ok or "human-only" not in str(error):
    fail(f"broker must refuse agent-full-access: {ok} {error!r}")
if settingslib.get_setting("agent-full-access") is not False:
    fail("broker-refused write changed agent-full-access")
ok, error = broker.op_set_setting({"key": "chaos-enabled",
                                   "value": True})
if ok or "human-only" not in str(error):
    fail(f"broker must refuse chaos-enabled: {ok} {error!r}")
ok, error = broker.op_set_setting({"key": "ui-dark", "value": "yes"})
if ok:
    fail("broker must refuse a wrong-typed value")
ok, error = broker.op_set_setting({"value": True})
if ok:
    fail("broker must refuse a missing key")
with open(os.environ["DCLAWOS_EVENTS_PATH"], "r",
          encoding="utf-8") as handle:
    events = handle.read()
if "settings_set ai-model" not in events or "via broker" not in events:
    fail("broker set-setting must log the change event")

# ------------------------------------------- two-user simulation (root)
# THE ATTACK from review P0 item 2, replayed: the agent identity
# must NOT be able to write the settings file directly — not an
# ordinary key, and above all not its own agent-full-access grant.
def _as_user(user, code):
    return subprocess.run(
        ["runuser", "-u", user, "--", "env",
         f"DCLAWOS_SETTINGS_FILE={SIM_FILE}",
         sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60)


SIM_DIR = "/var/lib/dclawos/settings"
SIM_FILE = os.path.join(SIM_DIR, "settingstest-sim.json")
have_users = False
if os.getuid() == 0 and shutil.which("runuser"):
    import pwd
    try:
        pwd.getpwnam("dclaw")
        pwd.getpwnam("dclaw-agent")
        have_users = os.path.isdir(SIM_DIR)
    except KeyError:
        have_users = False
if have_users:
    import pwd
    for suffix in ("", ".lock", ".corrupt"):
        try:
            os.unlink(SIM_FILE + suffix)
        except OSError:
            pass
    lib_dir = os.path.dirname(os.path.abspath(settingslib.__file__))
    read_code = (
        f"import sys; sys.path.insert(0, {lib_dir!r}); "
        "import settingslib, json; "
        "print(json.dumps(settingslib.load_settings()))")
    # Human (owner) writes — the GUI's path.
    proc = _as_user("dclaw",
                    f"import sys; sys.path.insert(0, {lib_dir!r}); "
                    "import settingslib; "
                    "settingslib.set_setting('ai-model', 'from-human')")
    if proc.returncode != 0:
        fail(f"dclaw could not write the settings file: "
             f"{proc.stderr.strip()[:200]}")
    st = os.stat(SIM_FILE)
    if st.st_uid != pwd.getpwnam("dclaw").pw_uid:
        fail("settings file must be owned by dclaw")
    if stat.S_IMODE(st.st_mode) != 0o640:
        fail(f"settings file mode {oct(stat.S_IMODE(st.st_mode))}, "
             f"want 0o640")
    # Agent reads it (group read) — reads are the shared half of B.
    proc = _as_user("dclaw-agent", read_code)
    if proc.returncode != 0 or '"from-human"' not in proc.stdout:
        fail(f"dclaw-agent could not READ the settings file: "
             f"{proc.stdout[:120]} {proc.stderr.strip()[:200]}")
    # ATTACK 1: the agent grants itself full access by writing
    # the file directly (the P0 hole). Must fail.
    proc = _as_user("dclaw-agent",
                    f"import sys; sys.path.insert(0, {lib_dir!r}); "
                    "import settingslib; "
                    "settingslib.set_setting('agent-full-access', True)")
    if proc.returncode == 0:
        fail("ATTACK SUCCEEDED: dclaw-agent wrote agent-full-access "
             "directly (P0 item 2 regression)")
    # ATTACK 2: an ordinary key, same direct path. Must also fail —
    # the agent's only write path is the broker.
    proc = _as_user("dclaw-agent",
                    f"import sys; sys.path.insert(0, {lib_dir!r}); "
                    "import settingslib; "
                    "settingslib.set_setting('ai-model', 'hacked')")
    if proc.returncode == 0:
        fail("ATTACK SUCCEEDED: dclaw-agent wrote the settings "
             "file directly")
    # ATTACK 3: replace the file wholesale via the parent dir —
    # the settings subdirectory (0710) must block even unlink.
    proc = _as_user("dclaw-agent",
                    f"import os; os.unlink({SIM_FILE!r})")
    if proc.returncode == 0:
        fail("ATTACK SUCCEEDED: dclaw-agent unlinked the settings "
             "file via the directory")
    proc = _as_user("dclaw-agent", read_code)
    if '"from-human"' not in proc.stdout or \
            '"agent-full-access": true' in proc.stdout:
        fail("settings content changed under attack")
    # A root broker write must preserve human ownership + mode,
    # and the human must still be able to write afterwards.
    saved = os.environ["DCLAWOS_SETTINGS_FILE"]
    os.environ["DCLAWOS_SETTINGS_FILE"] = SIM_FILE
    try:
        ok, result = broker.op_set_setting({"key": "ui-dark",
                                            "value": True})
    finally:
        os.environ["DCLAWOS_SETTINGS_FILE"] = saved
    if not ok:
        fail(f"broker write to the real settings file failed: "
             f"{result!r}")
    st = os.stat(SIM_FILE)
    if st.st_uid != pwd.getpwnam("dclaw").pw_uid or \
            stat.S_IMODE(st.st_mode) != 0o640:
        fail("broker write must restamp owner dclaw + mode 0640")
    proc = _as_user("dclaw",
                    f"import sys; sys.path.insert(0, {lib_dir!r}); "
                    "import settingslib; "
                    "settingslib.set_setting('ai-provider', 'after-broker')")
    if proc.returncode != 0:
        fail(f"dclaw locked out after a broker write: "
             f"{proc.stderr.strip()[:200]}")
    # An unrelated user cannot even open the file (0710 dir).
    proc = _as_user("nobody", f"open({SIM_FILE!r}).read()")
    if proc.returncode == 0:
        fail("unrelated user 'nobody' could read the settings file")
    for suffix in ("", ".lock", ".corrupt"):
        try:
            os.unlink(SIM_FILE + suffix)
        except OSError:
            pass
else:
    if os.environ.get("DCLAWOS_SETTINGSTEST_REQUIRE_SIM") == "1":
        fail("two-user simulation is REQUIRED in the image gate "
             "but its prerequisites (root, runuser, dclaw + "
             "dclaw-agent users, settings dir) are missing")
    print("settingstest: two-user simulation skipped "
          "(needs root + dclaw/dclaw-agent users)")

print("settingstest: all assertions passed")

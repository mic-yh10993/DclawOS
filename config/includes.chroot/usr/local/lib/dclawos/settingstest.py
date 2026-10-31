#!/usr/bin/env python3
"""Shared settings store selftest (Phase 5 follow-up P5-M7, build gate).

User decision 2026-10-31 option B: settings live in ONE file both
sides read (/var/lib/dclawos/agent-settings.json via settingslib).
Exercised here, against a temp file only:

- tool wiring alignment (toolslib schemas == agentlib.TOOLS) and
  classify() tiers: settings_get 0, settings_set 2;
- defaults on a missing file; one-time seed semantics (invalid
  seed entries skipped, second seed refused);
- cross-side round trip: a write through the lib is a read through
  the lib (the daemon/GUI share is the same lib by construction;
  the two-USER permission round trip below covers the OS side);
- HARD denylist: settings_set(agent=True) on agent-full-access and
  chaos-enabled fails closed; the human path still works, and
  agentlib.full_access_enabled() follows the file;
- validation: bad enum / bad type / unknown key / over-long string
  refused; a wrong-typed stored value falls back to its default;
- corrupt file: load returns defaults (daemon would still start),
  the next write sets the corrupt file aside as <path>.corrupt and
  succeeds;
- daemon dispatch functions (_settings_get/_settings_set from
  dclawos-agent): round trip + the settings_set audit event;
- two-user simulation (only when root with both users present,
  i.e. inside the image chroot): dclaw-agent writes, dclaw reads
  and writes back, an unrelated user (nobody) cannot even open it.

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
if mode != 0o660:
    fail(f"settings file mode {oct(mode)}, want 0o660")

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
result = daemon._settings_set({"key": "ai-model", "value": "gpt-x"},
                              {"session": "settingstest"})
if result["old"] != "" or result["new"] != "gpt-x":
    fail(f"daemon settings_set result mismatch: {result!r}")
expect_error(
    lambda: daemon._settings_set(
        {"key": "agent-full-access", "value": True},
        {"session": "settingstest"}),
    "human-only")
with open(os.environ["DCLAWOS_EVENTS_PATH"], "r",
          encoding="utf-8") as handle:
    events = handle.read()
if "settings_set ai-model" not in events:
    fail("successful settings_set must log a dashboard event")

# ------------------------------------------- two-user simulation (root)
def _as_user(user, code):
    return subprocess.run(
        ["runuser", "-u", user, "--", "env",
         f"DCLAWOS_SETTINGS_FILE={SIM_FILE}",
         sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60)


SIM_FILE = "/var/lib/dclawos/settingstest-sim.json"
have_users = False
if os.getuid() == 0 and shutil.which("runuser"):
    import pwd
    try:
        pwd.getpwnam("dclaw")
        pwd.getpwnam("dclaw-agent")
        have_users = os.path.isdir("/var/lib/dclawos")
    except KeyError:
        have_users = False
if have_users:
    for suffix in ("", ".lock"):
        try:
            os.unlink(SIM_FILE + suffix)
        except OSError:
            pass
    lib_dir = os.path.dirname(os.path.abspath(settingslib.__file__))
    write_code = (
        f"import sys; sys.path.insert(0, {lib_dir!r}); "
        "import settingslib; "
        "settingslib.set_setting('ai-model', 'from-agent')")
    proc = _as_user("dclaw-agent", write_code)
    if proc.returncode != 0:
        fail(f"dclaw-agent could not write the shared settings "
             f"file: {proc.stderr.strip()[:200]}")
    read_code = (
        f"import sys; sys.path.insert(0, {lib_dir!r}); "
        "import settingslib, json; "
        "print(json.dumps(settingslib.load_settings()))")
    proc = _as_user("dclaw", read_code)
    if proc.returncode != 0 or '"from-agent"' not in proc.stdout:
        fail(f"dclaw could not read the agent's write: "
             f"{proc.stdout[:120]} {proc.stderr.strip()[:200]}")
    write_code = (
        f"import sys; sys.path.insert(0, {lib_dir!r}); "
        "import settingslib; "
        "settingslib.set_setting('ui-dark', True)")
    proc = _as_user("dclaw", write_code)
    if proc.returncode != 0:
        fail(f"dclaw could not write the shared settings file: "
             f"{proc.stderr.strip()[:200]}")
    proc = _as_user("dclaw-agent", read_code)
    if '"ui-dark": true' not in proc.stdout:
        fail(f"dclaw-agent could not read the human's write: "
             f"{proc.stdout[:200]}")
    # An unrelated user must not be able to read the file at all
    # (its directory is group-gated; the file itself is 0660).
    proc = _as_user("nobody",
                    f"open({SIM_FILE!r}).read()")
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
             "dclaw-agent users) are missing")
    print("settingstest: two-user simulation skipped "
          "(needs root + dclaw/dclaw-agent users)")

print("settingstest: all assertions passed")

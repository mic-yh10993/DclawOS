#!/usr/bin/env python3
"""Model health/status reading (Phase 6 module M2). Stdlib only.

Real engine state lives under the engine state directory
(DCLAWOS_ENGINE_STATE_DIR, else DCLAWOS_STATE_DIR, else
/var/lib/dclawos/engine-state):
- provider-quality.json  (qualityStore.ts persistQuality)
- circuit-breakers.json  (stateStore.ts breaker snapshots)
- connection-rate-limits.json (dbAdapter.ts file-state adapter)

Preferred path: `dclawos-llm --status` (M2) reuses the engine's own
exports (getQualitySnapshot, getProviderBreakerState,
getAllModelLockouts) and returns JSON. When that binary is missing
or fails, this module falls back to reading the state FILES
directly — the same persisted data, minus in-process state.

HONEST LIMIT: model lockouts (accountFallback getAllModelLockouts)
live in the engine PROCESS memory. A fresh --status process starts
with an empty lockout map, and the daemon's engine calls are
short-lived CLI processes, so lockouts are effectively not
observable across processes. This module reports that limitation
verbatim (lockouts_available=False) instead of fabricating lockout
data. Missing/corrupt state files report "no data" per section,
never an exception.

// reuse-check: state file names + directory resolution ADOPTED
  from qualityStore.ts / stateStore.ts / dbAdapter.ts (see header);
  no new state format is invented. Breaker/quality semantics stay
  the engine's; this module only reads.
"""

import json
import os
import subprocess

STATE_DIR_DEFAULT = "/var/lib/dclawos/engine-state"
LLM_BIN_DEFAULT = "/usr/local/bin/dclawos-llm"
LOCKOUT_LIMIT_NOTE = (
    "模型锁定状态保存在引擎进程内存中（accountFallback），"
    "跨进程不可见；此处只呈现已持久化的质量、断路器与限流数据。")


def engine_state_dir():
    return (os.environ.get("DCLAWOS_ENGINE_STATE_DIR")
            or os.environ.get("DCLAWOS_STATE_DIR")
            or STATE_DIR_DEFAULT)


def _read_state_file(name):
    """Return (data_or_None, status) where status is
    'ok' | 'missing' | 'corrupt'."""
    path = os.path.join(engine_state_dir(), name)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return None, "missing"
    except (OSError, ValueError):
        return None, "corrupt"
    if not isinstance(data, (dict, list)):
        return None, "corrupt"
    return data, "ok"


def health_from_files():
    quality, q_status = _read_state_file("provider-quality.json")
    breakers, b_status = _read_state_file("circuit-breakers.json")
    limits, l_status = _read_state_file("connection-rate-limits.json")
    return {
        "source": "state-files",
        "quality": quality if isinstance(quality, dict) else {},
        "quality_status": q_status,
        "breakers": breakers if isinstance(breakers, (dict, list))
        else {},
        "breakers_status": b_status,
        "rate_limits": limits if isinstance(limits, (dict, list))
        else {},
        "rate_limits_status": l_status,
        "lockouts": [],
        "lockouts_available": False,
        "lockouts_note": LOCKOUT_LIMIT_NOTE,
    }


def health_status():
    """Engine status via dclawos-llm --status when available, else
    the state-file fallback. Never raises."""
    llm_bin = os.environ.get("DCLAWOS_LLM_BIN", LLM_BIN_DEFAULT)
    try:
        proc = subprocess.run(
            [llm_bin, "--status"], capture_output=True, text=True,
            timeout=20)
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            if isinstance(data, dict) and "quality" in data:
                data.setdefault("lockouts_available", False)
                data.setdefault("lockouts_note", LOCKOUT_LIMIT_NOTE)
                data.setdefault("source", "dclawos-llm --status")
                # Persisted sections the short-lived status process
                # cannot see better than the files: fill any empty
                # section from the files so one source never hides
                # the other.
                files = health_from_files()
                if not data.get("quality") and data.get("quality_file"):
                    data["quality"] = data["quality_file"]
                for key in ("breakers", "rate_limits"):
                    if not data.get(key):
                        data[key] = files[key]
                        data[f"{key}_status"] = files[f"{key}_status"]
                if not data.get("quality"):
                    data["quality"] = files["quality"]
                    data["quality_status"] = files["quality_status"]
                if not data.get("breakers") and data.get("breakers_file"):
                    data["breakers"] = data["breakers_file"]
                return data
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return health_from_files()

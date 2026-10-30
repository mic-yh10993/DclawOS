#!/usr/bin/env python3
"""Skills runtime (Phase 3 module 1). Standard library only.

A skill is a registry entry (see registrylib.py: skills entries carry
id/command/args/env/enabled/license, plus an optional path to a skill
directory holding SKILL.md). The runtime resolves an enabled entry,
runs its command with the caller's input on stdin, and returns capped
stdout/stderr plus the return code. Skills never run from arbitrary
paths: only enabled registry entries execute, and only with the exact
command + args recorded in the registry.

Lifecycle notes (evaluation cards §9, openclaw skills pattern):
- Discovery = the unified registry (Phase 2 module 9), not a TUI scan.
- Loading = entry resolution + SKILL.md description lookup (display
  only; the command is authoritative).
- Security = registry allow-list + agent approval tier (the daemon
  gates skill_run as tier 1 before this module runs) + env secrets
  via secret:<name> (same rule as mcpclient.py; values never logged).

Reuse notes:
- The skill directory layout (SKILL.md + scripts) follows OpenClaw's
  skills convention (config/discovery/loading); the runner itself is
  DclawOS-written in stdlib Python.
// reuse-check: openclaw skills runtime NOT copied, reason: the
  upstream runtime is TypeScript bound to the gateway bundle; the
  DclawOS service is stdlib Python, so only the layout convention
  transfers.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, "/usr/local/lib/dclawos")
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "lib", "dclawos"))

import agentlib  # noqa: E402

RUN_TIMEOUT_DEFAULT = 120
OUTPUT_CAP = 8192


class SkillError(Exception):
    pass


def load_skill_entry(skill_id):
    import registrylib
    data, _skipped = registrylib.load_registry()
    for entry in data.get("skills", []):
        if entry.get("id") == skill_id:
            if not entry.get("enabled", True):
                raise SkillError(f"skill disabled: {skill_id!r}")
            if not entry.get("command"):
                raise SkillError(f"skill {skill_id!r} has no command")
            return entry
    raise SkillError(f"unknown skill: {skill_id!r}")


def describe_skill(entry):
    """One-line description for display: SKILL.md first line, else id."""
    path = entry.get("path")
    if isinstance(path, str) and path:
        candidate = os.path.join(path, "SKILL.md")
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip().lstrip("#").strip()
                    if line:
                        return line[:200]
        except OSError:
            pass
    return entry.get("id", "")


def list_skills():
    import registrylib
    data, _skipped = registrylib.load_registry()
    out = []
    for entry in data.get("skills", []):
        out.append({
            "id": entry.get("id"),
            "enabled": bool(entry.get("enabled", True)),
            "license": entry.get("license", ""),
            "description": describe_skill(entry),
        })
    return out


def run_skill(params, _ticket_id):
    """Daemon entry point: params {skill, input, timeout}.

    The skill command runs with the input on stdin (never as argv, so
    input text cannot inject extra arguments). Output is capped.
    """
    skill_id = params.get("skill")
    if not isinstance(skill_id, str) or not skill_id:
        raise ValueError("skill_run needs a skill id")
    entry = load_skill_entry(skill_id)
    env = dict(os.environ)
    for key, value in (entry.get("env") or {}).items():
        if isinstance(value, str) and value.startswith("secret:"):
            env[key] = agentlib.read_secret(value[len("secret:"):])
        else:
            env[key] = value
    args = [entry["command"]] + list(entry.get("args") or [])
    timeout = params.get("timeout") or RUN_TIMEOUT_DEFAULT
    stdin_text = params.get("input") or ""
    if not isinstance(stdin_text, str):
        stdin_text = json.dumps(stdin_text, ensure_ascii=False)
    cwd = params.get("cwd") or agentlib.agent_workspace()
    try:
        proc = subprocess.run(args, input=stdin_text, cwd=cwd,
                              capture_output=True, text=True,
                              timeout=timeout, env=env)
    except FileNotFoundError:
        raise SkillError(f"skill command not found: {args[0]!r}")
    except subprocess.TimeoutExpired:
        raise SkillError(f"skill timed out after {timeout}s")
    except OSError as exc:
        raise SkillError(f"skill failed to run: {exc}")
    return {
        "skill": skill_id,
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-OUTPUT_CAP:],
        "stderr": (proc.stderr or "")[-OUTPUT_CAP:],
    }

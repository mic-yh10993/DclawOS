#!/usr/bin/env python3
"""LLM client bridge (Phase 4 module 2, gap rows A1/A7).

The Python agent service talks to models only through the
dclawos-llm CLI (Node, engine/llm.js): routing decision + dispatch +
executor + cost accounting live in the engine; this module owns the
local concerns — settings, secrets, and the subprocess contract.

Configuration (all local, GUI-managed; nothing calls a web UI):
- Provider/model/routing-mode come from the shared settings file
  (settingslib, P5-M7): the Settings AI panel writes the same file
  this service reads. (Before P5-M7 they were read via the
  gsettings CLI, which as dclaw-agent saw only dclaw-agent's own
  dconf defaults — the GUI's choices never arrived.)
- API keys are secret files named llm-<provider> in the agent
  secrets dir (written via the Settings secrets page / broker,
  0600). They are read here, passed to dclawos-llm ONLY via the
  DCLAWOS_LLM_KEYS environment variable (never argv, never logged,
  never in task files), and the CLI never echoes them.

Mock/testing: DCLAWOS_LLM_BIN overrides the CLI path and
DCLAWOS_LLM_BASE_URL_<PROVIDER> sets a per-provider endpoint
override, so the build gate can point the whole path at a local
mock OpenAI-compatible endpoint with a dummy key.
"""

import json
import os
import subprocess

import agentlib

LLM_BIN_DEFAULT = "/usr/local/bin/dclawos-llm"
SECRET_PREFIX = "llm-"
CALL_TIMEOUT = 300


def configured():
    """Return {provider, model, mode} from the shared settings
    file ('' = unset). Any settings failure degrades to unset,
    exactly like the old gsettings read failure did."""
    try:
        import settingslib  # local import (same dir)
        values = settingslib.load_settings()
    except Exception:  # noqa: BLE001 - unconfigured, not a crash
        values = {}
    return {
        "provider": values.get("ai-provider", ""),
        "model": values.get("ai-model", ""),
        "mode": values.get("routing-mode") or "auto",
    }


def available_keys():
    """Map provider -> key for every llm-<provider> secret file.

    Values are returned to the caller (the agent loop) only; callers
    must never log them or persist them.
    """
    keys = {}
    try:
        names = os.listdir(agentlib.secrets_dir())
    except OSError:
        return keys
    for name in sorted(names):
        if not name.startswith(SECRET_PREFIX):
            continue
        provider = name[len(SECRET_PREFIX):]
        if not provider:
            continue
        try:
            value = agentlib.read_secret(name)
        except (OSError, ValueError):
            continue
        if value:
            keys[provider] = value
    return keys


def provider_configured():
    """True when at least one provider key exists. The chat/loop
    layer uses this to stay honestly model-less when unconfigured."""
    return bool(available_keys())


def base_url_overrides(providers):
    overrides = {}
    for provider in providers:
        env_name = "DCLAWOS_LLM_BASE_URL_" + provider.upper().replace("-", "_")
        value = os.environ.get(env_name)
        if value:
            overrides[provider] = value
    return overrides


def call(messages, tools=None, task_type=None, provider=None,
         model=None, targets=None, max_tokens=None):
    """Run one model turn through dclawos-llm. Returns the result
    dict {response, provider, model, attempts, fallbackCount,
    costUsd, cost_envelope}. Raises ValueError on transport failure
    (the loop records it as data; a failed call is never disguised
    as a model reply)."""
    keys = available_keys()
    cfg = configured()
    request = {
        "messages": messages,
        "taskType": task_type or "default",
    }
    if tools:
        request["tools"] = tools
    if targets:
        request["targets"] = targets
    pin_provider = provider or cfg["provider"] or None
    pin_model = model or cfg["model"] or None
    if cfg["mode"] == "manual" or provider:
        if pin_provider:
            request["provider"] = pin_provider
        if pin_model:
            request["model"] = pin_model
    overrides = base_url_overrides(set(keys) | set(
        t["provider"] for t in (targets or [])))
    if overrides:
        request["baseUrlOverrides"] = overrides
    if max_tokens:
        request["maxTokens"] = max_tokens
    env = dict(os.environ)
    env["DCLAWOS_LLM_KEYS"] = json.dumps(keys)
    llm_bin = os.environ.get("DCLAWOS_LLM_BIN", LLM_BIN_DEFAULT)
    try:
        proc = subprocess.run(
            [llm_bin], input=json.dumps(request),
            capture_output=True, text=True, timeout=CALL_TIMEOUT,
            env=env)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        raise ValueError(f"dclawos-llm transport failed: {exc}")
    if proc.returncode != 0:
        raise ValueError(
            f"dclawos-llm exited {proc.returncode}: "
            f"{proc.stderr.strip()[:200]}")
    try:
        result = json.loads(proc.stdout)
    except ValueError:
        raise ValueError("dclawos-llm output not valid JSON")
    if not isinstance(result, dict):
        raise ValueError("dclawos-llm output not an object")
    return result

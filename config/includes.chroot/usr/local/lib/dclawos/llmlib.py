#!/usr/bin/env python3
"""LLM client bridge (Phase 4 module 2, gap rows A1/A7; Phase 6 M2 wiring).

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

Phase 6 M2 wiring (docs/PHASE6.md M2 ledger): the advanced model
configuration lives in the unified registry (registrylib:
providers = custom providers, model_config = routing / pins /
fallback / builtin overrides). This module assembles the FULL
request the engine already supports — strategy, routerStrategy,
modePack, weights, budgetCap, budgetFallback, explorationRate,
sla, lastKnownGoodProvider, maxTargets, ordered fallback targets,
baseUrlOverrides (builtin override + custom provider endpoints),
customPricing — from that registry. With NO model_config present
the assembled request is exactly the pre-M2 request (same keys,
same pin semantics), so existing callers are unaffected.

Pin selection order (tested in modeltest.py):
  explicit caller pin > per-task-type pin (model_config.pins)
  > global default (settings ai-provider / ai-model).
The subagent default pin is applied one level up, when the daemon
creates the child task (dclawos-agent _subagent), and only when the
child task carries no explicit pin of its own.

Custom providers: the routing decision only knows builtin
providers, so a custom pin/fallback target is passed as an explicit
targets[] entry plus a baseUrlOverrides entry (runLlm executes
explicit targets without a decision lookup); a custom provider is
only ever targeted when a llm-<provider> key file exists. Disabled
providers (custom disabled, or builtin override enabled=false) are
excluded from assembled targets; an explicit caller pin is always
honoured as-is (the caller asked for exactly that).

Every completed call appends one record to the durable usage
ledger (usagelib, /var/lib/dclawos/usage.jsonl); cost stays null
when the engine returned no cost envelope.

Phase 6 M3.1: call() and call_stream() share one no-key guard
(_short_circuit): with no provider key at all, or with an
explicit pin on a provider that holds no key, NO request is
attempted and the honest configured=False envelope is returned —
a keyless call must never carry a user prompt to an unconfigured
provider's default endpoint.

Mock/testing: DCLAWOS_LLM_BIN overrides the CLI path and
DCLAWOS_LLM_BASE_URL_<PROVIDER> sets a per-provider endpoint
override, so the build gate can point the whole path at a local
mock OpenAI-compatible endpoint with a dummy key.

// reuse-check: pin/routing field names ADOPTED from decision.ts
  DecideRoutingInput and llm.ts LlmRequest (strategy,
  routerStrategy, modePack, weights, budgetCap, budgetFallback,
  explorationRate, sla, lastKnownGoodProvider, maxTargets,
  targets, baseUrlOverrides); no parallel request vocabulary is
  invented. Settings/secret handling is the pre-M2 code, unchanged
  in semantics.
"""

import json
import os
import subprocess
import time

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


def model_config():
    """The registry model_config singleton (defaults when absent).
    Never raises: a broken registry degrades to defaults, which
    reproduce the pre-M2 request shape."""
    try:
        import registrylib
        data, _skipped = registrylib.load_registry()
        return data.get("model_config") \
            or registrylib.default_model_config(), data
    except Exception:  # noqa: BLE001 - degrade to pre-M2 behaviour
        try:
            import registrylib
            return registrylib.default_model_config(), {
                "providers": [], "model_config": None}
        except Exception:  # noqa: BLE001
            return {}, {"providers": []}


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


def key_configured(provider):
    return provider in available_keys()


def base_url_overrides(providers):
    overrides = {}
    for provider in providers:
        env_name = "DCLAWOS_LLM_BASE_URL_" + provider.upper().replace("-", "_")
        value = os.environ.get(env_name)
        if value:
            overrides[provider] = value
    return overrides


def resolve_pin(task_type=None, provider=None, model=None):
    """Resolve the effective (provider, model, source) for a call.

    Order: explicit caller pin > model_config pin for the task
    type > global settings default. A pin level may supply only
    one of provider/model; the other falls through to the next
    level (per-field resolution, matching the pre-M2 behaviour
    where provider and model were independent settings)."""
    cfg, _data = model_config()
    pins = cfg.get("pins") or {}
    task_pin = pins.get(task_type or "default") or {}
    settings = configured()
    out_provider = provider or task_pin.get("provider") \
        or settings["provider"] or None
    out_model = model or task_pin.get("model") \
        or settings["model"] or None
    if provider:
        source = "explicit"
    elif task_pin.get("provider") or task_pin.get("model"):
        source = "task-pin"
    elif settings["provider"] or settings["model"]:
        source = "global"
    else:
        source = "none"
    return out_provider, out_model, source


def _provider_maps(data, cfg):
    """Return (custom_by_id, disabled_ids, override_by_id)."""
    custom = {p["id"]: p for p in data.get("providers", [])}
    overrides = cfg.get("provider_overrides") or {}
    disabled = set()
    for pid, entry in custom.items():
        if not entry.get("enabled", True):
            disabled.add(pid)
    for pid, overlay in overrides.items():
        if not overlay.get("enabled", True):
            disabled.add(pid)
    return custom, disabled, overrides


def build_request(messages, tools=None, task_type=None, provider=None,
                  model=None, targets=None, max_tokens=None):
    """Assemble the dclawos-llm request dict. Pure assembly (keys
    are consulted only for provider eligibility, never copied into
    the request). Exposed for the headless gate (modeltest.py)."""
    keys = available_keys()
    cfg, data = model_config()
    settings = configured()
    custom, disabled, overrides = _provider_maps(data, cfg)
    request = {
        "messages": messages,
        "taskType": task_type or "default",
    }
    if tools:
        request["tools"] = tools
    pin_provider, pin_model, _source = resolve_pin(
        task_type, provider, model)

    # --- targets: caller targets win; else pin (when custom) +
    #     configured fallback order; else let the engine decide.
    assembled_targets = None
    if targets:
        assembled_targets = [dict(t) for t in targets]
    else:
        chain = []
        if pin_provider and pin_provider in custom:
            # A custom provider is invisible to decideRouting; it
            # can only be served as an explicit target.
            if pin_provider not in disabled and pin_provider in keys:
                chain.append({"provider": pin_provider,
                              "model": pin_model
                              or (custom[pin_provider].get("models")
                                  or [{}])[0].get("id", "")})
        for item in cfg.get("fallback") or []:
            pid = item.get("provider")
            if not pid or pid in disabled or pid not in keys:
                continue
            entry = {"provider": pid, "model": item.get("model")}
            if entry not in chain:
                chain.append(entry)
        if chain:
            assembled_targets = chain
    if assembled_targets:
        request["targets"] = assembled_targets

    # --- provider/model pins for the engine decision path.
    # A custom pin must NOT be forwarded as provider= (decideRouting
    # would reject an unknown provider); it travels via targets.
    # Forwarding rule per field (preserves pre-M2 semantics for the
    # global default, which the engine only honoured in manual
    # mode): explicit and task-type pins always forward; the global
    # default forwards only in manual mode or when targets were
    # assembled (the decision is then skipped anyway).
    task_pin = (cfg.get("pins") or {}).get(task_type or "default") or {}
    forward_provider = bool(provider or task_pin.get("provider")
                            or settings["mode"] == "manual"
                            or assembled_targets)
    forward_model = bool(model or task_pin.get("model")
                         or settings["mode"] == "manual"
                         or assembled_targets)
    if forward_provider and pin_provider and pin_provider not in custom:
        request["provider"] = pin_provider
    if forward_model and pin_model:
        request["model"] = pin_model

    # --- routing parameters (only when actually configured, so a
    #     default registry reproduces the pre-M2 request exactly).
    if cfg.get("strategy"):
        request["strategy"] = cfg["strategy"]
    if cfg.get("routerStrategy"):
        request["routerStrategy"] = cfg["routerStrategy"]
    if cfg.get("modePack"):
        request["modePack"] = cfg["modePack"]
    if cfg.get("weights"):
        request["weights"] = cfg["weights"]
    if cfg.get("budgetCap") is not None:
        request["budgetCap"] = cfg["budgetCap"]
    if cfg.get("budgetFallback"):
        request["budgetFallback"] = cfg["budgetFallback"]
    if cfg.get("explorationRate") is not None:
        request["explorationRate"] = cfg["explorationRate"]
    if cfg.get("sla"):
        request["sla"] = cfg["sla"]
    if cfg.get("lastKnownGoodProvider"):
        request["lastKnownGoodProvider"] = cfg["lastKnownGoodProvider"]
    if cfg.get("maxTargets") is not None:
        request["maxTargets"] = cfg["maxTargets"]

    # --- endpoint overrides: env (tests) < builtin override <
    #     custom provider baseUrl (most specific wins last).
    relevant = set(keys) | {t["provider"] for t in
                            (assembled_targets or [])}
    if pin_provider:
        relevant.add(pin_provider)
    merged_overrides = base_url_overrides(relevant)
    for pid in relevant:
        overlay = overrides.get(pid)
        if overlay and overlay.get("baseUrl"):
            merged_overrides[pid] = overlay["baseUrl"]
        if pid in custom and custom[pid].get("baseUrl"):
            merged_overrides[pid] = custom[pid]["baseUrl"]
    if merged_overrides:
        request["baseUrlOverrides"] = merged_overrides

    # --- custom pricing (consumed by llm.ts computeCost ahead of
    #     the registry pricing table).
    pricing = {}
    for pid, entry in custom.items():
        for m in entry.get("models", []):
            if m.get("pricing"):
                pricing[f"{pid}::{m['id']}"] = m["pricing"]
    for pid, overlay in overrides.items():
        for m in overlay.get("addModels", []):
            if m.get("pricing"):
                pricing[f"{pid}::{m['id']}"] = m["pricing"]
    if pricing:
        request["customPricing"] = pricing
    if custom:
        # Phase 6 M3: the engine's vendored registry has no entry
        # for a custom provider, so its format (openai / passthrough
        # / claude) travels with the request; llm.ts consults it
        # only for providers the registry does not know.
        request["providerFormats"] = {
            pid: entry.get("format", "openai")
            for pid, entry in custom.items()}
    if max_tokens:
        request["maxTokens"] = max_tokens
    return request


def _record_usage(result, task_type, task_id, session):
    try:
        import usagelib
        envelope = result.get("cost_envelope") or {}
        usage = None
        raw_usage = envelope.get("usage") if isinstance(
            envelope, dict) else None
        if isinstance(raw_usage, dict):
            usage = {
                "prompt_tokens": raw_usage.get("promptTokens", 0),
                "completion_tokens": raw_usage.get(
                    "completionTokens", 0),
            }
        usagelib.append_usage(
            result.get("provider"), result.get("model"),
            result.get("costUsd"), usage=usage,
            attempts=len(result.get("attempts") or []),
            task_id=task_id, session=session or "",
            task_type=task_type, ok=result.get("response") is not None)
    except Exception:  # noqa: BLE001 - ledger never breaks a call
        pass


def _no_key_envelope():
    """The honest empty envelope returned when a call is
    short-circuited before any request: nothing was sent, nothing
    is recorded in the usage ledger, cost stays null."""
    return {"response": None, "provider": None, "model": None,
            "attempts": [], "fallbackCount": 0, "costUsd": None,
            "cost_envelope": None, "configured": False}


def _short_circuit(keys, provider):
    """Shared no-key short-circuit for call() / call_stream()
    (Phase 6 M3.1). Returns the honest empty envelope when the
    call must not be attempted at all, else None:

    (a) NO provider key exists at all (the M2 test_connection
        rule; the M3 correction applied it to call_stream), or
    (b) the caller pinned a provider explicitly and that provider
        holds no key — the engine pushes an explicit pin as a
        target even keyless and would attempt the provider's
        DEFAULT endpoint unauthenticated (the mechanism behind
        the 2026-10-31 10:27 permission-card incident), carrying
        the user's prompt with it.

    Auto routing (no explicit pin) with at least one key present
    proceeds to the CLI exactly as before.
    // reuse-check: semantics ADOPTED from the M3 correction's
      call_stream short-circuit and the M2 test_connection no-key
      rule — one helper, one envelope, no second semantics.
    """
    if not keys:
        return _no_key_envelope()
    if provider and provider not in keys:
        return _no_key_envelope()
    return None


def call(messages, tools=None, task_type=None, provider=None,
         model=None, targets=None, max_tokens=None, task_id=None,
         session=None):
    """Run one model turn through dclawos-llm. Returns the result
    dict {response, provider, model, attempts, fallbackCount,
    costUsd, cost_envelope}. When _short_circuit fires (no keys
    at all, or an explicit pin on a keyless provider) returns the
    honest empty envelope with configured=False instead — no CLI
    is spawned and no usage is recorded. Raises ValueError on
    transport failure (the loop records it as data; a failed call
    is never disguised as a model reply)."""
    keys = available_keys()
    short = _short_circuit(keys, provider)
    if short is not None:
        return short
    request = build_request(
        messages, tools=tools, task_type=task_type,
        provider=provider, model=model, targets=targets,
        max_tokens=max_tokens)
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
    _record_usage(result, task_type, task_id, session)
    return result


def call_stream(messages, tools=None, task_type=None, provider=None,
                model=None, targets=None, max_tokens=None,
                task_id=None, session=None, on_delta=None):
    """Streaming variant of call() (Phase 6 M3).

    Same request assembly (build_request) and the SAME usage
    ledger write (_record_usage, exactly once per completed call)
    as call(); the difference is transport: dclawos-llm is run
    with --stream and its NDJSON stdout is consumed line by line —
    each {"type":"delta", ...} line is forwarded to
    on_delta(delta_dict) as it arrives, and the final
    {"type":"result", ...} line becomes the return value (the
    non-streaming result shape plus stream_mode / partial).
    Keys travel only via the DCLAWOS_LLM_KEYS env var, exactly as
    in call(). Raises ValueError on transport failure.
    // reuse-check: subprocess + env-key contract ADOPTED from
      call() above; the NDJSON line protocol is dclawos-llm
      --stream's own output shape (see that CLI), not a second
      streaming protocol invented here.
    """
    import threading as _threading
    keys = available_keys()
    short = _short_circuit(keys, provider)
    if short is not None:
        # No-key short-circuit (shared with call() since Phase 6
        # M3.1; see _short_circuit): NO request is sent and no CLI
        # is spawned, and nothing is recorded in the usage ledger
        # because no call happened.
        short["stream_mode"] = "staged"
        return short
    request = build_request(
        messages, tools=tools, task_type=task_type,
        provider=provider, model=model, targets=targets,
        max_tokens=max_tokens)
    request["stream"] = True
    env = dict(os.environ)
    env["DCLAWOS_LLM_KEYS"] = json.dumps(keys)
    llm_bin = os.environ.get("DCLAWOS_LLM_BIN", LLM_BIN_DEFAULT)
    try:
        proc = subprocess.Popen(
            [llm_bin, "--stream"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env)
    except (FileNotFoundError, OSError) as exc:
        raise ValueError(f"dclawos-llm transport failed: {exc}")
    timer = _threading.Timer(CALL_TIMEOUT, proc.kill)
    timer.daemon = True
    timer.start()
    result = None
    try:
        try:
            proc.stdin.write(json.dumps(request))
            proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "delta":
                if on_delta is not None:
                    try:
                        on_delta(item)
                    except Exception:  # noqa: BLE001 - a UI-side
                        pass              # callback must not kill
            elif item.get("type") == "result":  # the model call
                result = {k: v for k, v in item.items()
                          if k != "type"}
        proc.wait(timeout=10)
    except Exception as exc:  # noqa: BLE001 - normalised below
        try:
            proc.kill()
        except OSError:
            pass
        raise ValueError(f"dclawos-llm stream failed: {exc}")
    finally:
        timer.cancel()
    if proc.returncode not in (0, None):
        stderr = ""
        try:
            stderr = (proc.stderr.read() or "")[:200]
        except Exception:  # noqa: BLE001
            stderr = ""
        raise ValueError(
            f"dclawos-llm exited {proc.returncode}: {stderr.strip()}")
    if result is None:
        raise ValueError("dclawos-llm stream produced no result line")
    _record_usage(result, task_type, task_id, session)
    return result


def test_connection(provider, model=None):
    """One minimal real request against a chosen provider/model
    (M2 connectivity test). No key = no request is sent and no
    success is fabricated. Returns {ok, configured, provider,
    model, attempts, latency_ms, cost_usd, error}."""
    if not provider:
        return {"ok": False, "configured": False, "provider": None,
                "model": model, "attempts": 0, "latency_ms": None,
                "cost_usd": None, "error": "未选择供应商"}
    if not key_configured(provider):
        return {"ok": False, "configured": False, "provider": provider,
                "model": model, "attempts": 0, "latency_ms": None,
                "cost_usd": None,
                "error": "未配置密钥：请先为该供应商保存 llm-"
                         f"{provider} 密钥"}
    cfg, data = model_config()
    custom, _disabled, _overrides = _provider_maps(data, cfg)
    use_model = model
    if not use_model and provider in custom:
        models = custom[provider].get("models") or []
        use_model = models[0]["id"] if models else None
    if provider in custom and not use_model:
        return {"ok": False, "configured": True,
                "provider": provider, "model": None, "attempts": 0,
                "latency_ms": None, "cost_usd": None,
                "error": "自定义供应商未配置任何模型"}
    # The test targets EXACTLY the chosen provider/model: an
    # explicit single target, so configured fallback chains cannot
    # mask a failure of the tested target (or fake its success).
    targets = [{"provider": provider, "model": use_model}] \
        if use_model else None
    started = time.time()
    try:
        result = call(
            [{"role": "user", "content": "ping"}],
            task_type="default", provider=provider, model=use_model,
            targets=targets, max_tokens=8)
    except ValueError as exc:
        return {"ok": False, "configured": True, "provider": provider,
                "model": use_model, "attempts": 0,
                "latency_ms": int((time.time() - started) * 1000),
                "cost_usd": None, "error": str(exc)[:300]}
    latency = int((time.time() - started) * 1000)
    attempts = result.get("attempts") or []
    if result.get("response") is not None:
        return {"ok": True, "configured": True,
                "provider": result.get("provider") or provider,
                "model": result.get("model") or use_model,
                "attempts": len(attempts), "latency_ms": latency,
                "cost_usd": result.get("costUsd"), "error": None}
    error = None
    if attempts:
        last = attempts[-1]
        error = last.get("error") or (
            f"status {last.get('status')} "
            f"({last.get('failureKind')})")
    return {"ok": False, "configured": True,
            "provider": result.get("provider") or provider,
            "model": use_model, "attempts": len(attempts),
            "latency_ms": latency, "cost_usd": result.get("costUsd"),
            "error": error or "所有尝试均失败，无响应"}

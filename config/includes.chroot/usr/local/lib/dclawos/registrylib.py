#!/usr/bin/env python3
"""Unified skills + MCP server registry (Phase 2 module 9, stdlib only).

User-confirmed Q3: skills and MCP are UNIFIED — one registry file, one
library, one GUI page in the settings app, no TUI.

File: /var/lib/dclawos/agent-registry.json (overridable via
DCLAWOS_REGISTRY for tests):
  {"skills": [{id, name, path, description, license, enabled}],
   "mcp_servers": [{id, name, command, args, env, description,
                     license, enabled}]}
IDs match ^[a-z0-9][a-z0-9_-]*$ and are unique within their list.

Scope notes (read before extending):
- This is the MANAGEMENT layer (list / add / remove / enable), not
  the MCP protocol client: full MCP client protocol + tool-call
  approval live in the agent service (later module) and reuse the
  module 5 ticket broker for approval semantics (cards §9).
- The GUI does NOT edit MCP env vars in module 9: env values are
  often secrets, and the secure home for them is libsecret (a later
  addition). The schema already carries env (preserved round-trip)
  so hand-written registries and the future agent service can use it;
  the GUI warns that env is not editable here rather than storing
  secrets in plaintext through a form.
- License policy (cards §9 "third-party skill package licenses checked
  per item"): every entry carries a license field, shown in the GUI.
  Adding an entry WITHOUT a license pops a confirmation dialog — the
  per-item check. No third-party packages are bundled in module 9, so
  there is nothing to check yet; the mechanism is ready.
- load_registry() returns (data, skipped): malformed entries (not a
  dict, or missing a required field) are skipped and counted, never
  silently kept; a corrupt whole file raises ValueError (the GUI
  shows it instead of an empty list pretending all is well).
"""

import grp
import json
import math
import os
import re

REGISTRY_DEFAULT = "/var/lib/dclawos/agent-registry.json"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SHARED_GROUP_DEFAULT = "dclawos-session"

# --- Phase 6 M2: model configuration constants -------------------------
# // reuse-check: factor keys, mode packs, strategies and SLA shape are
#   ADOPTED from the vendored engine sources, not invented here:
#   DEFAULT_WEIGHTS keys in open-sse/services/autoCombo/scoring.ts,
#   MODE_PACKS keys in open-sse/services/autoCombo/modePacks.ts,
#   strategyRegistry registrations in
#   open-sse/services/autoCombo/routerStrategy.ts (rules/score/cost/
#   eco/latency/fast/sla-aware/sla/lkgp; "nadir" deliberately NOT
#   registered upstream and therefore NOT accepted here),
#   SlaRoutingPolicy in the same file, FITNESS_TABLE keys in
#   open-sse/services/autoCombo/taskFitness.ts (coding/review/
#   planning/analysis/debugging/documentation + default; there is
#   no "chat" or "research" task type in the engine, so none is
#   invented here). Weight validation mirrors the engine's
#   validateWeights (scoring.ts): every value a finite number and
#   the total within 0.01 of 1.0; unknown keys are rejected here
#   because the engine's normalizeScoringWeights would silently
#   drop them, hiding typos. Cross-checked against the real engine
#   in modeltest.py via dclawos-route when that binary is present.
WEIGHT_KEYS = (
    "quota", "health", "costInv", "latencyInv", "taskFit",
    "stability", "tierPriority", "tierAffinity", "specificityMatch",
    "contextAffinity", "cacheAffinity", "sessionAvailability",
    "resetWindowAffinity", "connectionDensity", "quality",
    "reliability",
)
MODE_PACKS = (
    "ship-fast", "cost-saver", "quality-first", "offline-friendly",
    "reliability-first", "chaos-mode",
)
ROUTER_STRATEGIES = (
    "rules", "score", "cost", "eco", "latency", "fast",
    "sla-aware", "sla", "lkgp",
)
TASK_TYPES = (
    "default", "coding", "review", "planning", "analysis",
    "debugging", "documentation",
)
PROVIDER_FORMATS = ("openai", "passthrough", "claude")
BUDGET_FALLBACKS = ("cheapest", "strict")

MODEL_CONFIG_DEFAULT = {
    "strategy": "",
    "routerStrategy": "",
    "modePack": "",
    "weights": None,
    "budgetCap": None,
    "budgetFallback": "",
    "explorationRate": None,
    "sla": None,
    "lastKnownGoodProvider": "",
    "maxTargets": None,
    "fallback": [],
    "pins": {},
    "subagent_pin": {},
    "provider_overrides": {},
}


def registry_path():
    return os.environ.get("DCLAWOS_REGISTRY", REGISTRY_DEFAULT)


def _set_shared_group(path):
    try:
        os.chown(path, -1,
                 grp.getgrnam(os.environ.get(
                     "DCLAWOS_SHARED_GROUP", SHARED_GROUP_DEFAULT)).gr_gid)
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


def _validate_id(entry_id):
    if not isinstance(entry_id, str) or not ID_RE.match(entry_id):
        raise ValueError(
            f"invalid id: {entry_id!r} "
            "(lowercase letters, digits, _ and -, must start alnum)")


def _normalize(entry, required, defaults):
    if not isinstance(entry, dict):
        raise ValueError("entry is not an object")
    normalized = dict(entry)
    for field in required:
        value = normalized.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"entry missing required field: {field}")
    _validate_id(normalized["id"])
    for field, default in defaults.items():
        if field not in normalized:
            normalized[field] = default
    if "enabled" in normalized and not isinstance(
            normalized["enabled"], bool):
        raise ValueError("entry 'enabled' must be true or false")
    return normalized


def normalize_skill(entry):
    normalized = _normalize(
        entry, ("id", "name", "path"),
        {"description": "", "license": "", "enabled": True})
    return normalized


def normalize_server(entry):
    # Phase 5: a server is either stdio (command required) or
    # streamable HTTP (url required, transport == "http"). The
    # original shape (id/name/command) is unchanged for stdio.
    normalized = _normalize(
        entry, ("id", "name"),
        {"command": "", "args": [], "env": {}, "description": "",
         "license": "", "enabled": True, "transport": "stdio",
         "url": ""})
    if normalized["transport"] == "http":
        url = normalized["url"]
        if not isinstance(url, str) or not url.startswith(
                ("http://", "https://")):
            raise ValueError(
                "http mcp server needs an http(s) 'url'")
    elif not isinstance(normalized["command"], str) or \
            not normalized["command"]:
        raise ValueError("entry missing required field: command")
    if not isinstance(normalized["args"], list) or not all(
            isinstance(a, str) for a in normalized["args"]):
        raise ValueError("server 'args' must be a list of strings")
    if not isinstance(normalized["env"], dict):
        raise ValueError("server 'env' must be an object")
    return normalized


# Phase 3 module 2: connectors are a third registry category, with the
# same entry shape as MCP servers (id/name/command required, optional
# args/env/description/license/enabled). A connector is a command-based
# channel adapter: the agent service sends it JSON on stdin
# ({action: "send", message}) and reads JSON on stdout. Unknown fields
# round-trip like the other categories.
def normalize_connector(entry):
    normalized = _normalize(
        entry, ("id", "name", "command"),
        {"args": [], "env": {}, "description": "", "license": "",
         "enabled": True})
    if not isinstance(normalized["args"], list) or not all(
            isinstance(a, str) for a in normalized["args"]):
        raise ValueError("connector 'args' must be a list of strings")
    if not isinstance(normalized["env"], dict):
        raise ValueError("connector 'env' must be an object")
    return normalized


# Phase 5 (WS1): channels are a fourth registry category. A channel
# is a third-party messaging service (Telegram, Discord, ...) the
# agent daemon polls through channellib's built-in adapters —
# unlike connectors, a channel is NOT an arbitrary command: "kind"
# selects a built-in adapter module, "secret" names the agent
# secret file holding the bot token (written only by the human via
# the broker write-secret op), and "allowlist" holds the owner
# account ids allowed to talk to the agent (sender ids as strings;
# an empty allowlist lets NOBODY in). Unknown fields round-trip.
def normalize_channel(entry):
    normalized = _normalize(
        entry, ("id", "name", "kind"),
        {"description": "", "license": "", "enabled": True,
         "allowlist": [], "secret": "", "options": {}})
    allowlist = normalized["allowlist"]
    if not isinstance(allowlist, list) or not all(
            isinstance(a, (str, int)) for a in allowlist):
        raise ValueError("channel 'allowlist' must be a list of ids")
    normalized["allowlist"] = [str(a) for a in allowlist]
    if not isinstance(normalized["secret"], str):
        raise ValueError("channel 'secret' must be a string")
    if not isinstance(normalized["options"], dict):
        raise ValueError("channel 'options' must be an object")
    return normalized


# --- Phase 6 M2: custom providers + model configuration ----------------
# // reuse-check: entry normalisation reuses this module's _normalize /
#   _validate_id discipline (id shape, defaults, enabled bool) rather
#   than a parallel validator; the model-config field semantics reuse
#   decision.ts DecideRoutingInput field-for-field.

def _validate_url(value, field):
    if not isinstance(value, str) or not value.startswith(
            ("http://", "https://")):
        raise ValueError(f"{field} must be an http(s) URL, got {value!r}")
    if len(value) > 512 or any(ch.isspace() for ch in value):
        raise ValueError(f"{field} is not a usable URL: {value!r}")
    return value


def _validate_pricing(value, field="pricing"):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object or null")
    out = {}
    for key in ("inputPer1M", "outputPer1M"):
        raw = value.get(key)
        if raw is None:
            out[key] = None
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) \
                or not math.isfinite(raw) or raw < 0:
            raise ValueError(f"{field}.{key} must be a finite number >= 0")
        out[key] = float(raw)
    return out


def _normalize_model_ref(entry, field):
    if not isinstance(entry, dict):
        raise ValueError(f"{field} must be an object {{id, name?, pricing?}}")
    mid = entry.get("id")
    if not isinstance(mid, str) or not mid or len(mid) > 200:
        raise ValueError(f"{field}.id must be a non-empty string")
    name = entry.get("name", "")
    if not isinstance(name, str):
        raise ValueError(f"{field}.name must be a string")
    return {"id": mid, "name": name,
            "pricing": _validate_pricing(entry.get("pricing"),
                                         f"{field}.pricing")}


def normalize_provider(entry):
    """One CUSTOM provider: full CRUD shape (M2). Builtin providers
    are not stored here; they are only overridden (see
    normalize_provider_override)."""
    normalized = _normalize(
        entry, ("id", "name", "baseUrl"),
        {"description": "", "license": "", "enabled": True,
         "format": "openai", "models": []})
    _validate_url(normalized["baseUrl"], "provider baseUrl")
    if normalized["format"] not in PROVIDER_FORMATS:
        raise ValueError(
            f"provider format must be one of {list(PROVIDER_FORMATS)}, "
            f"got {normalized['format']!r}")
    models = normalized["models"]
    if not isinstance(models, list):
        raise ValueError("provider 'models' must be a list")
    seen = set()
    clean_models = []
    for item in models:
        model = _normalize_model_ref(item, "provider model")
        if model["id"] in seen:
            raise ValueError(f"duplicate model id: {model['id']!r}")
        seen.add(model["id"])
        clean_models.append(model)
    normalized["models"] = clean_models
    return normalized


def normalize_provider_override(entry):
    """Overlay for one BUILTIN provider: enable/disable, endpoint
    override, model add/hide. Deleting the overlay = restore default."""
    if not isinstance(entry, dict):
        raise ValueError("provider override must be an object")
    out = {"enabled": True, "baseUrl": "", "addModels": [],
           "hideModels": []}
    if "enabled" in entry:
        if not isinstance(entry["enabled"], bool):
            raise ValueError("override 'enabled' must be true or false")
        out["enabled"] = entry["enabled"]
    base = entry.get("baseUrl", "")
    if base:
        out["baseUrl"] = _validate_url(base, "override baseUrl")
    elif not isinstance(base, str):
        raise ValueError("override 'baseUrl' must be a string")
    adds = entry.get("addModels", [])
    if not isinstance(adds, list):
        raise ValueError("override 'addModels' must be a list")
    out["addModels"] = [_normalize_model_ref(m, "override addModel")
                        for m in adds]
    hides = entry.get("hideModels", [])
    if not isinstance(hides, list) or not all(
            isinstance(h, str) and h for h in hides):
        raise ValueError("override 'hideModels' must be a list of ids")
    out["hideModels"] = list(hides)
    return out


def validate_weights(weights):
    """Mirror of the engine's validateWeights (scoring.ts) plus the
    key-set check its normalizeScoringWeights implies. Returns the
    cleaned dict or raises ValueError with a readable message."""
    if not isinstance(weights, dict) or not weights:
        raise ValueError("weights must be a non-empty object")
    unknown = sorted(set(weights) - set(WEIGHT_KEYS))
    if unknown:
        raise ValueError(
            f"unknown weight keys {unknown}; the engine has exactly "
            f"the 16 factors {list(WEIGHT_KEYS)}")
    total = 0.0
    clean = {}
    for key, value in weights.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not math.isfinite(value) or value < 0:
            raise ValueError(f"weight {key!r} must be a finite number >= 0")
        clean[key] = float(value)
        total += float(value)
    if abs(total - 1.0) >= 0.01:
        raise ValueError(
            f"weights must sum to 1.0 (±0.01, engine validateWeights); "
            f"got {total:.4f}")
    return clean


def _validate_pin(value, field):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object {{provider, model}}")
    provider = value.get("provider", "")
    model = value.get("model", "")
    if not isinstance(provider, str) or not isinstance(model, str):
        raise ValueError(f"{field}.provider/.model must be strings")
    if len(provider) > 200 or len(model) > 200:
        raise ValueError(f"{field} provider/model too long")
    if not provider and not model:
        return {}
    return {"provider": provider, "model": model}


def normalize_model_config(raw):
    """Validate + normalise the model_config singleton. Raises
    ValueError on any bad field: callers must refuse to persist a
    bad config rather than store a value the engine would ignore."""
    if raw is None:
        return json.loads(json.dumps(MODEL_CONFIG_DEFAULT))
    if not isinstance(raw, dict):
        raise ValueError("model_config must be an object")
    cfg = json.loads(json.dumps(MODEL_CONFIG_DEFAULT))
    for field in ("strategy", "routerStrategy"):
        value = raw.get(field, "")
        if not isinstance(value, str):
            raise ValueError(f"model_config.{field} must be a string")
        if value and value not in ROUTER_STRATEGIES:
            raise ValueError(
                f"model_config.{field} must be one of "
                f"{list(ROUTER_STRATEGIES)} (nadir is not registered "
                f"in the engine), got {value!r}")
        cfg[field] = value
    pack = raw.get("modePack", "")
    if not isinstance(pack, str) or (pack and pack not in MODE_PACKS):
        raise ValueError(
            f"model_config.modePack must be one of {list(MODE_PACKS)} "
            f"or empty, got {pack!r}")
    cfg["modePack"] = pack
    if raw.get("weights") is not None:
        cfg["weights"] = validate_weights(raw["weights"])
    cap = raw.get("budgetCap")
    if cap is not None:
        if isinstance(cap, bool) or not isinstance(cap, (int, float)) \
                or not math.isfinite(cap) or cap < 0:
            raise ValueError("model_config.budgetCap must be a number >= 0")
        cfg["budgetCap"] = float(cap)
    fallback_mode = raw.get("budgetFallback", "")
    if not isinstance(fallback_mode, str) or (
            fallback_mode and fallback_mode not in BUDGET_FALLBACKS):
        raise ValueError(
            f"model_config.budgetFallback must be one of "
            f"{list(BUDGET_FALLBACKS)} or empty")
    cfg["budgetFallback"] = fallback_mode
    rate = raw.get("explorationRate")
    if rate is not None:
        if isinstance(rate, bool) or not isinstance(rate, (int, float)) \
                or not math.isfinite(rate) or not 0 <= rate <= 1:
            raise ValueError(
                "model_config.explorationRate must be a number in [0, 1]")
        cfg["explorationRate"] = float(rate)
    sla = raw.get("sla")
    if sla is not None:
        if not isinstance(sla, dict):
            raise ValueError("model_config.sla must be an object or null")
        clean_sla = {}
        for key in ("targetP95Ms", "maxCostPer1MTokens"):
            if sla.get(key) is not None:
                value = sla[key]
                if isinstance(value, bool) or not isinstance(
                        value, (int, float)) or not math.isfinite(value) \
                        or value <= 0:
                    raise ValueError(f"sla.{key} must be a number > 0")
                clean_sla[key] = float(value)
        if sla.get("maxErrorRate") is not None:
            value = sla["maxErrorRate"]
            if isinstance(value, bool) or not isinstance(
                    value, (int, float)) or not math.isfinite(value) \
                    or not 0 <= value <= 1:
                raise ValueError("sla.maxErrorRate must be in [0, 1]")
            clean_sla["maxErrorRate"] = float(value)
        if sla.get("hardConstraints") is not None:
            if not isinstance(sla["hardConstraints"], bool):
                raise ValueError("sla.hardConstraints must be a boolean")
            clean_sla["hardConstraints"] = sla["hardConstraints"]
        unknown_sla = sorted(set(sla) - {"targetP95Ms", "maxErrorRate",
                                         "maxCostPer1MTokens",
                                         "hardConstraints"})
        if unknown_sla:
            raise ValueError(f"unknown sla keys: {unknown_sla}")
        cfg["sla"] = clean_sla or None
    lkgp = raw.get("lastKnownGoodProvider", "")
    if not isinstance(lkgp, str) or len(lkgp) > 200:
        raise ValueError(
            "model_config.lastKnownGoodProvider must be a string")
    cfg["lastKnownGoodProvider"] = lkgp
    max_targets = raw.get("maxTargets")
    if max_targets is not None:
        if isinstance(max_targets, bool) or not isinstance(
                max_targets, int) or not 1 <= max_targets <= 20:
            raise ValueError(
                "model_config.maxTargets must be an integer in [1, 20]")
        cfg["maxTargets"] = max_targets
    fallback = raw.get("fallback", [])
    if not isinstance(fallback, list):
        raise ValueError("model_config.fallback must be a list")
    clean_fallback = []
    for item in fallback:
        pin = _validate_pin(item, "fallback target")
        if not pin.get("provider") or not pin.get("model"):
            raise ValueError(
                "each fallback target needs both provider and model")
        clean_fallback.append(pin)
    cfg["fallback"] = clean_fallback
    pins = raw.get("pins", {})
    if not isinstance(pins, dict):
        raise ValueError("model_config.pins must be an object")
    clean_pins = {}
    for task_type, pin in pins.items():
        if task_type not in TASK_TYPES:
            raise ValueError(
                f"unknown pin task type {task_type!r}; engine task "
                f"types are {list(TASK_TYPES)}")
        clean = _validate_pin(pin, f"pins.{task_type}")
        if clean:
            clean_pins[task_type] = clean
    cfg["pins"] = clean_pins
    cfg["subagent_pin"] = _validate_pin(
        raw.get("subagent_pin"), "subagent_pin")
    overrides = raw.get("provider_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError("model_config.provider_overrides must be an object")
    clean_overrides = {}
    for provider_id, overlay in overrides.items():
        _validate_id(provider_id)
        clean_overrides[provider_id] = normalize_provider_override(overlay)
    cfg["provider_overrides"] = clean_overrides
    return cfg


def default_model_config():
    return json.loads(json.dumps(MODEL_CONFIG_DEFAULT))


def add_provider(data, entry):
    normalized = normalize_provider(entry)
    if _find(data, "providers", normalized["id"]) is not None:
        raise ValueError(
            f"provider id already exists: {normalized['id']!r}")
    data.setdefault("providers", []).append(normalized)
    return normalized


def upsert_provider(data, entry):
    normalized = normalize_provider(entry)
    existing = _find(data, "providers", normalized["id"])
    if existing is not None:
        data["providers"][data["providers"].index(existing)] = normalized
    else:
        data.setdefault("providers", []).append(normalized)
    return normalized


def merged_providers(catalog_providers, data):
    """Merge the engine catalog (builtin, read-only) with registry
    custom providers + builtin overrides into the page/runtime view.
    Each entry: {id, name, builtin, enabled, baseUrl, format, models,
    has_override}. Pure function: catalog entries are dicts shaped
    like dclawos-route --catalog providers ({id, models:[{id,name}]}).
    // reuse-check: catalog shape ADOPTED from decision.ts
      buildCatalog output; no second catalog format is invented."""
    cfg = data.get("model_config") or default_model_config()
    overrides = cfg.get("provider_overrides", {})
    out = []
    builtin_ids = set()
    for entry in catalog_providers or []:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        pid = entry["id"]
        builtin_ids.add(pid)
        overlay = overrides.get(pid)
        models = []
        hidden = set((overlay or {}).get("hideModels", []))
        for model in entry.get("models", []):
            if isinstance(model, dict) and model.get("id"):
                if model["id"] in hidden:
                    continue
                models.append({"id": model["id"],
                               "name": model.get("name") or "",
                               "pricing": None})
        if overlay:
            for added in overlay.get("addModels", []):
                if added["id"] not in {m["id"] for m in models}:
                    models.append(dict(added))
        out.append({
            "id": pid, "name": pid, "builtin": True,
            "enabled": bool(overlay["enabled"]) if overlay else True,
            "baseUrl": (overlay or {}).get("baseUrl", ""),
            "format": "", "models": models,
            "has_override": overlay is not None,
        })
    for entry in data.get("providers", []):
        if entry["id"] in builtin_ids:
            continue  # a custom entry may not shadow a builtin id
        out.append({
            "id": entry["id"], "name": entry.get("name", entry["id"]),
            "builtin": False,
            "enabled": bool(entry.get("enabled", True)),
            "baseUrl": entry.get("baseUrl", ""),
            "format": entry.get("format", "openai"),
            "models": [dict(m) for m in entry.get("models", [])],
            "has_override": False,
        })
    return out


def load_registry(path=None):
    if path is None:
        path = registry_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return {"skills": [], "mcp_servers": [], "connectors": [],
                "channels": [], "providers": [],
                "model_config": default_model_config()}, 0
    except (OSError, ValueError) as exc:
        raise ValueError(f"registry unreadable: {exc}")
    if not isinstance(data, dict):
        raise ValueError("registry root is not an object")
    result = {"skills": [], "mcp_servers": [], "connectors": [],
              "channels": [], "providers": [],
              "model_config": default_model_config()}
    skipped = 0
    for kind, normalizer in (("skills", normalize_skill),
                             ("mcp_servers", normalize_server),
                             ("connectors", normalize_connector),
                             ("channels", normalize_channel),
                             ("providers", normalize_provider)):
        entries = data.get(kind, [])
        if not isinstance(entries, list):
            raise ValueError(f"registry field {kind!r} is not a list")
        for entry in entries:
            try:
                result[kind].append(normalizer(entry))
            except ValueError:
                skipped += 1
    if "model_config" in data:
        try:
            result["model_config"] = normalize_model_config(
                data.get("model_config"))
        except ValueError:
            skipped += 1
    # Unknown top-level fields round-trip (forward compatibility,
    # same spirit as unknown entry fields in the other categories).
    for key, value in data.items():
        if key not in result:
            result[key] = value
    return result, skipped


def save_registry(data, path=None):
    if path is None:
        path = registry_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    _atomic_write(
        path,
        json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n")


def _find(data, kind, entry_id):
    for entry in data.get(kind, []):
        if entry.get("id") == entry_id:
            return entry
    return None


def add_skill(data, entry):
    normalized = normalize_skill(entry)
    if _find(data, "skills", normalized["id"]) is not None:
        raise ValueError(f"skill id already exists: {normalized['id']!r}")
    data["skills"].append(normalized)
    return normalized


def add_server(data, entry):
    normalized = normalize_server(entry)
    if _find(data, "mcp_servers", normalized["id"]) is not None:
        raise ValueError(
            f"mcp server id already exists: {normalized['id']!r}")
    data["mcp_servers"].append(normalized)
    return normalized


def add_connector(data, entry):
    normalized = normalize_connector(entry)
    if _find(data, "connectors", normalized["id"]) is not None:
        raise ValueError(
            f"connector id already exists: {normalized['id']!r}")
    data["connectors"].append(normalized)
    return normalized


def add_channel(data, entry):
    normalized = normalize_channel(entry)
    if _find(data, "channels", normalized["id"]) is not None:
        raise ValueError(
            f"channel id already exists: {normalized['id']!r}")
    data["channels"].append(normalized)
    return normalized


def remove_entry(data, kind, entry_id):
    entry = _find(data, kind, entry_id)
    if entry is None:
        raise ValueError(f"no such {kind[:-1]}: {entry_id!r}")
    data[kind].remove(entry)


def set_enabled(data, kind, entry_id, enabled):
    entry = _find(data, kind, entry_id)
    if entry is None:
        raise ValueError(f"no such {kind[:-1]}: {entry_id!r}")
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be true or false")
    entry["enabled"] = enabled
    return entry

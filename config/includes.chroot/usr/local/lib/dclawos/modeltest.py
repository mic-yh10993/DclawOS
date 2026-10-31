#!/usr/bin/env python3
"""Model-settings wiring gate (Phase 6 module M2). Headless.

Only touches a temp dir. A fake dclawos-llm (a small Python script
that really POSTs to the request's baseUrlOverrides endpoint, like
the executor does) plus a local mock OpenAI-compatible HTTP server
exercise the REAL llmlib assembly path:

1. registrylib model validation: bad weights (wrong keys, bad sum,
   non-finite), bad provider URL/format, bad SLA/pin types are
   refused; a valid full model_config round-trips.
2. CRUD -> llmlib assembly: a custom provider pinned for a call
   lands in targets with its baseUrl override and custom pricing;
   a builtin override endpoint wins for that provider; a disabled
   provider is excluded from assembled fallback targets.
3. Pin selection order: explicit > task-type pin > global default;
   with no config at all the request shape is the pre-M2 shape.
4. Routing pass-through: strategy/routerStrategy/modePack/weights/
   budgetCap/budgetFallback/explorationRate/sla/
   lastKnownGoodProvider/maxTargets/fallback order all reach the
   captured request JSON.
5. Connectivity test via llmlib.test_connection against the local
   mock: success path, failure path (mock 429), and the no-key
   path (no request is sent, configured=false).
6. Usage ledger: records from those calls aggregate with token
   totals; a null-cost record is counted as unknown cost, never 0;
   a corrupt ledger line is tolerated and counted.
7. Bridge allowlist: the new model.* verbs validate on the
   settings surface, are refused on chat/dashboard, unregistered
   verbs are refused, and replies strip secret-looking keys.
8. Health status: missing state dir reports no-data sections;
   corrupt files are tolerated; a valid quality file is surfaced.

Exits non-zero on the first failed assertion. Invoked by build
hook 1008 inside the image chroot; also runnable by hand.

// reuse-check: fake-llm + mock-server pattern ADOPTED from
  agentlooptest.py / dclawos-llm --selftest (local mock endpoint,
  dummy key, no external network); assertions are M2-specific.
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/usr/local/lib/dclawos")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = tempfile.mkdtemp(prefix="dclawos-modeltest-")
REGISTRY = os.path.join(ROOT, "registry.json")
SETTINGS = os.path.join(ROOT, "settings", "agent-settings.json")
SECRETS = os.path.join(ROOT, "secrets")
USAGE = os.path.join(ROOT, "usage.jsonl")
STATE = os.path.join(ROOT, "engine-state")
CAPTURE = os.path.join(ROOT, "capture.json")
FAKE_BIN = os.path.join(ROOT, "fake-llm")
os.makedirs(SECRETS, exist_ok=True)
os.makedirs(STATE, exist_ok=True)
os.environ["DCLAWOS_REGISTRY"] = REGISTRY
os.environ["DCLAWOS_SETTINGS_FILE"] = SETTINGS
os.environ["DCLAWOS_SECRETS_DIR"] = SECRETS
os.environ["DCLAWOS_USAGE_FILE"] = USAGE
os.environ["DCLAWOS_ENGINE_STATE_DIR"] = STATE
os.environ["DCLAWOS_STATE_DIR"] = STATE
os.environ["DCLAWOS_TASKS_DIR"] = os.path.join(ROOT, "tasks")
os.environ["DCLAWOS_EVENTS_PATH"] = os.path.join(ROOT, "events.jsonl")
os.environ["DCLAWOS_LLM_BIN"] = FAKE_BIN

import bridgeprotocol  # noqa: E402
import llmlib  # noqa: E402
import modellib  # noqa: E402
import registrylib  # noqa: E402
import settingslib  # noqa: E402
import usagelib  # noqa: E402


def fail(message):
    print(f"modeltest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def expect_error(fn, needle):
    try:
        fn()
    except ValueError as exc:
        if needle and needle not in str(exc):
            fail(f"error {exc!r} does not mention {needle!r}")
        return
    fail(f"expected ValueError mentioning {needle!r}, got success")


# ------------------------------------------------------------- mock API
MOCK_HITS = []


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}
        MOCK_HITS.append({"path": self.path, "model": body.get("model")})
        self.send_response(429 if body.get("model") == "mock-fail" else 200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body.get("model") == "mock-fail":
            payload = {"error": {"message": "quota exhausted"}}
        else:
            payload = {
                "id": "mock-1", "model": body.get("model"),
                "choices": [{"index": 0, "message": {
                    "role": "assistant", "content": "mock-reply"},
                    "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def log_message(self, *_args):
        pass


SERVER = HTTPServer(("127.0.0.1", 0), MockHandler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()
MOCK_URL = f"http://127.0.0.1:{SERVER.server_address[1]}/v1/chat/completions"

# ------------------------------------------------------------- fake llm
FAKE_LLM = r'''
import json, os, sys, urllib.request, urllib.error
capture = os.environ.get("FAKE_CAPTURE", "")
raw = sys.stdin.read()
req = json.loads(raw)
if capture:
    with open(capture, "w", encoding="utf-8") as fh:
        fh.write(raw)
targets = req.get("targets") or []
if not targets and req.get("provider"):
    targets = [{"provider": req["provider"], "model": req.get("model") or "auto"}]
if not targets:
    # Mimic runLlm's decision fallback: first keyed provider serves.
    try:
        _keys = json.loads(os.environ.get("DCLAWOS_LLM_KEYS") or "{}")
    except ValueError:
        _keys = {}
    if _keys:
        _prov = sorted(_keys)[0]
        targets = [{"provider": _prov, "model": req.get("model") or "gpt-4o-mini"}]
attempts = []
served = None
response = None
for target in targets:
    url = (req.get("baseUrlOverrides") or {}).get(target["provider"])
    if not url:
        attempts.append({"provider": target["provider"], "model": target["model"],
                         "status": 0, "failureKind": "durable",
                         "error": "no endpoint"})
        continue
    body = json.dumps({"model": target["model"], "messages": req.get("messages") or []}).encode()
    http_req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(http_req, timeout=10) as resp:
            response = json.loads(resp.read().decode())
            status = resp.status
    except urllib.error.HTTPError as exc:
        attempts.append({"provider": target["provider"], "model": target["model"],
                         "status": exc.code, "failureKind": "durable",
                         "error": "mock error"})
        continue
    attempts.append({"provider": target["provider"], "model": target["model"],
                     "status": status, "failureKind": None})
    served = target
    break
cost = None
envelope = None
if response is not None and served is not None:
    usage = response.get("usage") or {}
    pricing = (req.get("customPricing") or {}).get(served["provider"] + "::" + served["model"])
    if pricing and pricing.get("inputPer1M") is not None and pricing.get("outputPer1M") is not None:
        cost = round((usage.get("prompt_tokens", 0) / 1e6) * pricing["inputPer1M"]
                     + (usage.get("completion_tokens", 0) / 1e6) * pricing["outputPer1M"], 6)
    envelope = {"estimated": cost, "actual": cost, "currency": "USD",
                "priced": cost is not None,
                "usage": {"promptTokens": usage.get("prompt_tokens", 0),
                          "completionTokens": usage.get("completion_tokens", 0)}}
print(json.dumps({"response": response,
                  "provider": served["provider"] if served else None,
                  "model": served["model"] if served else None,
                  "attempts": attempts,
                  "fallbackCount": max(0, len(attempts) - 1),
                  "costUsd": cost, "cost_envelope": envelope}))
'''
with open(FAKE_BIN, "w", encoding="utf-8") as handle:
    handle.write("#!/usr/bin/env python3\n" + FAKE_LLM)
os.chmod(FAKE_BIN, 0o755)
os.environ["FAKE_CAPTURE"] = CAPTURE


def write_secret(name, value):
    with open(os.path.join(SECRETS, name), "w", encoding="utf-8") as fh:
        fh.write(value)


def captured():
    with open(CAPTURE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_config(cfg):
    data, _skipped = registrylib.load_registry()
    data["model_config"] = registrylib.normalize_model_config(cfg)
    registrylib.save_registry(data)


def main():
    # ---- 1. validation refusals + valid round trip
    expect_error(lambda: registrylib.validate_weights(
        {"quota": 0.5, "bogus": 0.5}), "unknown weight")
    expect_error(lambda: registrylib.validate_weights(
        {"quota": 0.5, "health": 0.4}), "sum to 1.0")
    expect_error(lambda: registrylib.validate_weights(
        {"quota": float("nan"), "health": 1.0}), "finite")
    good_weights = {k: 0.0 for k in registrylib.WEIGHT_KEYS}
    good_weights["quota"] = 0.5
    good_weights["health"] = 0.5
    if registrylib.validate_weights(good_weights)["quota"] != 0.5:
        fail("valid weights rejected")
    expect_error(lambda: registrylib.normalize_provider(
        {"id": "x1", "name": "X", "baseUrl": "ftp://bad",
         "format": "openai", "models": []}), "http")
    expect_error(lambda: registrylib.normalize_provider(
        {"id": "x1", "name": "X", "baseUrl": "https://ok.example/v1",
         "format": "grpc", "models": []}), "format")
    expect_error(lambda: registrylib.normalize_model_config(
        {"modePack": "no-such-pack"}), "modePack")
    expect_error(lambda: registrylib.normalize_model_config(
        {"routerStrategy": "nadir"}), "nadir")
    expect_error(lambda: registrylib.normalize_model_config(
        {"pins": {"chat": {"provider": "a", "model": "b"}}}),
        "task type")
    expect_error(lambda: registrylib.normalize_model_config(
        {"explorationRate": 1.5}), "explorationRate")
    expect_error(lambda: registrylib.normalize_model_config(
        {"sla": {"targetP95Ms": -5}}), "sla")
    full_cfg = {
        "strategy": "rules", "routerStrategy": "sla-aware",
        "modePack": "cost-saver", "weights": good_weights,
        "budgetCap": 2.5, "budgetFallback": "strict",
        "explorationRate": 0.1,
        "sla": {"targetP95Ms": 2000, "maxErrorRate": 0.05,
                "maxCostPer1MTokens": 10, "hardConstraints": True},
        "lastKnownGoodProvider": "openai", "maxTargets": 4,
        "fallback": [{"provider": "openai", "model": "gpt-4o-mini"}],
        "pins": {"coding": {"provider": "openai",
                            "model": "gpt-4o-mini"}},
        "subagent_pin": {"provider": "openai", "model": "gpt-4o-mini"},
        "provider_overrides": {},
    }
    if registrylib.normalize_model_config(full_cfg)["modePack"] \
            != "cost-saver":
        fail("valid full model_config rejected")

    # ---- 2/3/4. assembly, pins, routing pass-through
    settingslib.seed({"ai-provider": "openai",
                      "ai-model": "gpt-4o-mini",
                      "routing-mode": "auto"})
    os.environ["DCLAWOS_LLM_BASE_URL_OPENAI"] = MOCK_URL
    write_secret("llm-openai", "dummy-openai-key")
    write_secret("llm-mycustom", "dummy-custom-key")
    write_secret("llm-offprov", "dummy-off-key")
    data, _skipped = registrylib.load_registry()
    registrylib.add_provider(data, {
        "id": "mycustom", "name": "My Custom",
        "baseUrl": MOCK_URL, "format": "openai",
        "models": [{"id": "my-model", "name": "",
                    "pricing": {"inputPer1M": 1.0,
                                "outputPer1M": 2.0}}],
        "enabled": True})
    registrylib.add_provider(data, {
        "id": "offprov", "name": "Off", "baseUrl": MOCK_URL,
        "format": "openai",
        "models": [{"id": "off-model", "name": "", "pricing": None}],
        "enabled": False})
    registrylib.save_registry(data)

    # No model_config yet: pre-M2 request shape. Global default in
    # auto mode is NOT forwarded as a pin (old semantics).
    result = llmlib.call([{"role": "user", "content": "hi"}])
    req = captured()
    for key in ("strategy", "routerStrategy", "modePack", "weights",
                "budgetCap", "explorationRate", "sla", "maxTargets"):
        if key in req:
            fail(f"default request gained routing key {key}")
    if "provider" in req:
        fail("auto-mode global default was forwarded as a pin")
    if not result.get("response"):
        fail("default call against mock failed")

    # Full config: routing fields reach the request; task pin for
    # coding beats the global default; explicit pin beats task pin.
    cfg = dict(full_cfg)
    cfg["provider_overrides"] = {
        "openai": {"enabled": True, "baseUrl": MOCK_URL,
                   "addModels": [], "hideModels": []}}
    cfg["fallback"] = [
        {"provider": "offprov", "model": "off-model"},
        {"provider": "openai", "model": "gpt-4o-mini"}]
    save_config(cfg)
    llmlib.call([{"role": "user", "content": "code please"}],
                task_type="coding")
    req = captured()
    if req.get("provider") != "openai" or req.get("model") != "gpt-4o-mini":
        fail(f"task pin not applied: {req.get('provider')}/{req.get('model')}")
    for key, want in (("strategy", "rules"),
                      ("routerStrategy", "sla-aware"),
                      ("modePack", "cost-saver"),
                      ("budgetCap", 2.5), ("budgetFallback", "strict"),
                      ("explorationRate", 0.1), ("maxTargets", 4),
                      ("lastKnownGoodProvider", "openai")):
        if req.get(key) != want:
            fail(f"routing field {key} not passed: {req.get(key)!r}")
    if req.get("weights") != good_weights:
        fail("weights not passed through intact")
    if req.get("sla", {}).get("targetP95Ms") != 2000.0:
        fail("sla not passed through")
    targets = req.get("targets") or []
    target_providers = [t["provider"] for t in targets]
    if "offprov" in target_providers:
        fail("disabled provider was not excluded from targets")
    if target_providers != ["openai"]:
        fail(f"fallback targets wrong: {target_providers}")
    if (req.get("baseUrlOverrides") or {}).get("openai") != MOCK_URL:
        fail("builtin override baseUrl not applied")

    llmlib.call([{"role": "user", "content": "hi"}],
                task_type="coding", provider="openai",
                model="explicit-model")
    req = captured()
    if req.get("model") != "explicit-model":
        fail("explicit pin did not beat task pin")

    # Custom provider pin: travels via targets + baseUrl override +
    # custom pricing, NOT via provider= (decision would reject it).
    result = llmlib.call([{"role": "user", "content": "hi"}],
                         provider="mycustom", model="my-model")
    req = captured()
    if "provider" in req and req.get("provider") == "mycustom":
        fail("custom pin leaked into provider= for decideRouting")
    if not any(t["provider"] == "mycustom" for t in req.get("targets", [])):
        fail("custom provider missing from targets")
    if (req.get("baseUrlOverrides") or {}).get("mycustom") != MOCK_URL:
        fail("custom baseUrl override missing")
    if (req.get("customPricing") or {}).get("mycustom::my-model") is None:
        fail("custom pricing not assembled")
    # 100 prompt x $1/1M + 50 completion x $2/1M = 0.0002
    if result.get("costUsd") is None or abs(result["costUsd"] - 0.0002) > 1e-9:
        fail(f"custom pricing cost wrong: {result.get('costUsd')}")

    # resolve_pin order, directly.
    provider, model, source = llmlib.resolve_pin("coding")
    if (provider, model, source) != ("openai", "gpt-4o-mini", "task-pin"):
        fail(f"resolve_pin task level wrong: {(provider, model, source)}")
    provider, model, source = llmlib.resolve_pin("default")
    if source != "global":
        fail(f"resolve_pin global level wrong: {source}")

    # ---- 5. connectivity: success / failure / no-key
    ok = llmlib.test_connection("openai", "gpt-4o-mini")
    if not ok["ok"] or ok["provider"] != "openai" \
            or ok["latency_ms"] is None:
        fail(f"connectivity success path wrong: {ok}")
    if ok["cost_usd"] is not None:
        fail("unpriced mock call must report null cost, not a number")
    bad = llmlib.test_connection("openai", "mock-fail")
    if bad["ok"] or not bad["error"]:
        fail(f"connectivity failure path wrong: {bad}")
    hits_before = len(MOCK_HITS)
    nokey = llmlib.test_connection("nokeyprov", "whatever")
    if nokey["ok"] or nokey["configured"] or "未配置密钥" not in nokey["error"]:
        fail(f"no-key path wrong: {nokey}")
    if len(MOCK_HITS) != hits_before:
        fail("no-key connectivity test sent a request")

    # ---- 6. usage ledger aggregation (records written by llmlib)
    usagelib.append_usage("openai", "gpt-4o-mini", None,
                          usage={"prompt_tokens": 10,
                                 "completion_tokens": 5},
                          attempts=1)
    with open(USAGE, "a", encoding="utf-8") as handle:
        handle.write("{corrupt line\n")
    agg = usagelib.aggregate_usage()
    if agg["total_calls"] < 5:
        fail(f"usage ledger missing call records: {agg['total_calls']}")
    if agg["bad_lines"] != 1:
        fail(f"corrupt usage line not counted: {agg['bad_lines']}")
    if agg["unknown_cost_count"] < 2:
        fail("null-cost calls not counted separately as unknown")
    custom_rows = [r for r in agg["by_model"]
                   if r["provider"] == "mycustom"
                   and r["model"] == "my-model"]
    if not custom_rows or abs(custom_rows[0]["known_cost_usd"] - 0.0002) > 1e-9:
        fail(f"custom provider usage aggregation wrong: {custom_rows}")
    if agg["prompt_tokens"] <= 0 or agg["completion_tokens"] <= 0:
        fail("token totals not aggregated")

    # ---- 7. bridge allowlist for the M2 verbs
    for verb in ("model.catalog", "model.provider.save",
                 "model.provider.delete", "model.provider.override",
                 "model.provider.reset", "model.config.get",
                 "model.config.save", "model.health", "model.test",
                 "model.usage"):
        good = bridgeprotocol.validate_command(
            {"kind": "command", "protocol": 1, "id": "m1",
             "command": verb, "params": {}}, "settings")
        if good["command"] != verb:
            fail(f"M2 verb {verb} refused on settings surface")
        try:
            bridgeprotocol.validate_command(
                {"kind": "command", "protocol": 1, "id": "m2",
                 "command": verb, "params": {}}, "chat")
            fail(f"M2 verb {verb} accepted on chat surface")
        except bridgeprotocol.BridgeError:
            pass
    try:
        bridgeprotocol.validate_command(
            {"kind": "command", "protocol": 1, "id": "m3",
             "command": "model.evil", "params": {}}, "settings")
        fail("unregistered model verb accepted")
    except bridgeprotocol.BridgeError:
        pass
    reply = bridgeprotocol.make_reply(
        "m1", {"api_key": "should-not-echo", "provider": "openai"})
    if "should-not-echo" in json.dumps(reply):
        fail("bridge reply echoes a secret-looking key")

    # ---- 8. health status tolerance
    health = modellib.health_status()
    if health.get("quality_status") not in ("missing", "corrupt", "ok"):
        fail("health status lacks a quality_status")
    if health.get("lockouts_available") is not False:
        fail("health must honestly report lockouts unavailable")
    with open(os.path.join(STATE, "provider-quality.json"),
              "w", encoding="utf-8") as handle:
        handle.write("{not json")
    health = modellib.health_from_files()
    if health["quality_status"] != "corrupt" or health["quality"] != {}:
        fail("corrupt quality file not tolerated as no-data")
    with open(os.path.join(STATE, "provider-quality.json"),
              "w", encoding="utf-8") as handle:
        json.dump({"openai::gpt-4o-mini": {
            "provider": "openai", "model": "gpt-4o-mini",
            "samples": 7, "successEwma": 0.9,
            "latencyEwmaMs": 800}}, handle)
    health = modellib.health_from_files()
    entry = health["quality"].get("openai::gpt-4o-mini") or {}
    if entry.get("samples") != 7:
        fail("valid quality file not surfaced")

    # Engine presence cross-check when the built bundle is present
    # (in the image chroot it always is): the engine's own selftest
    # validates DEFAULT_WEIGHTS + every mode pack with the real
    # validateWeights — the semantics registrylib.validate_weights
    # mirrors in Python (documented in registrylib's reuse-check).
    route_bin = "/usr/local/bin/dclawos-route"
    if os.path.isfile(route_bin) and os.access(route_bin, os.X_OK):
        import subprocess
        proc = subprocess.run(
            [route_bin, "--selftest"], capture_output=True,
            text=True, timeout=60)
        if proc.returncode != 0:
            fail(f"engine selftest failed: {proc.stderr[:200]}")

    print("modeltest OK: registry validation, CRUD->assembly, pin "
          "order, routing pass-through, connectivity "
          "success/failure/no-key, usage aggregation, bridge "
          "allowlist and health tolerance verified "
          "(local mock only, no real provider contacted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

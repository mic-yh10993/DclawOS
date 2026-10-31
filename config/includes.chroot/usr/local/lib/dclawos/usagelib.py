#!/usr/bin/env python3
"""Durable per-call usage ledger (Phase 6 module M2). Stdlib only.

Before M2 there was NO persistent usage record: task results carried
a summed cost, but no per-call history existed to aggregate honestly.
This module is that ledger. llmlib.call appends one JSON line per
completed dclawos-llm invocation:

  {ts, provider, model, task_id, session, task_type,
   usage: {prompt_tokens, completion_tokens} | null,
   cost_usd: number | null, attempts: int, ok: bool}

Honesty rules (binding):
- cost_usd stays NULL when the engine produced no cost envelope
  (unpriced model, no response). It is never coerced to 0 and never
  estimated here; aggregation counts null-cost calls separately as
  "unknown cost".
- History before this version has no per-call records and is NOT
  reconstructed from event text: the settings page states that
  recording starts with this version.
- Aggregation tolerates corrupt lines (counted as bad_lines).

File: /var/lib/dclawos/usage.jsonl (env override DCLAWOS_USAGE_FILE),
append-only, shared-group mode following the events.jsonl pattern.

// reuse-check: append + group/mode handling ADOPTED from
  agentlib.log_event / agentlib.atomic_write conventions (same
  shared exchange directory, group dclawos-session, 0660), reason:
  the ledger lives beside events.jsonl and must be readable by the
  human-side settings host and writable by the agent daemon.
// reuse-check: cost semantics ADOPTED from llm.ts CostEnvelope
  (actual cost only from a real response's usage x pricing);
  this module records, it never computes a price.
"""

import grp
import json
import os
import time

USAGE_DEFAULT = "/var/lib/dclawos/usage.jsonl"
SHARED_GROUP_DEFAULT = "dclawos-session"


def usage_path():
    return os.environ.get("DCLAWOS_USAGE_FILE", USAGE_DEFAULT)


def _set_shared_group(path):
    try:
        os.chown(path, -1, grp.getgrnam(
            os.environ.get("DCLAWOS_SHARED_GROUP",
                           SHARED_GROUP_DEFAULT)).gr_gid)
    except (KeyError, OSError):
        pass


def append_usage(provider, model, cost_usd, usage=None, attempts=0,
                 task_id=None, session="", task_type=None, ok=True):
    """Append one call record. Best effort: a ledger failure must
    never fail the model call itself (the caller already has its
    result); returns True when the line was written."""
    record = {
        "ts": int(time.time()),
        "provider": provider or None,
        "model": model or None,
        "task_id": task_id or None,
        "session": session or "",
        "task_type": task_type or "default",
        "usage": usage if isinstance(usage, dict) else None,
        "cost_usd": cost_usd if isinstance(cost_usd, (int, float))
        and not isinstance(cost_usd, bool) else None,
        "attempts": int(attempts) if isinstance(attempts, int) else 0,
        "ok": bool(ok),
    }
    path = usage_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        _set_shared_group(path)
        try:
            os.chmod(path, 0o660)
        except OSError:
            pass
        return True
    except OSError:
        return False


def read_usage():
    """Return (records, bad_lines). Missing file = ([], 0)."""
    records = []
    bad = 0
    try:
        with open(usage_path(), "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return [], 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if not isinstance(item, dict):
            bad += 1
            continue
        records.append(item)
    return records, bad


def aggregate_usage(records=None):
    """Aggregate records by provider and by (provider, model).

    Returns {total_calls, bad_lines, known_cost_usd,
    unknown_cost_count, prompt_tokens, completion_tokens,
    by_provider: [...], by_model: [...]}. Null costs are counted in
    unknown_cost_count and never added to known_cost_usd.
    """
    if records is None:
        records, bad = read_usage()
    else:
        bad = 0
    by_provider = {}
    by_model = {}

    def _bucket(store, key):
        return store.setdefault(key, {
            "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "known_cost_usd": 0.0, "unknown_cost_count": 0,
        })

    total_prompt = 0
    total_completion = 0
    known_total = 0.0
    unknown_total = 0
    for rec in records:
        provider = rec.get("provider") or "(none)"
        model = rec.get("model") or "(none)"
        usage = rec.get("usage") or {}
        prompt = usage.get("prompt_tokens") or 0
        completion = usage.get("completion_tokens") or 0
        if not isinstance(prompt, (int, float)):
            prompt = 0
        if not isinstance(completion, (int, float)):
            completion = 0
        cost = rec.get("cost_usd")
        known = isinstance(cost, (int, float)) \
            and not isinstance(cost, bool)
        for store, key in ((by_provider, provider),
                           (by_model, f"{provider}::{model}")):
            bucket = _bucket(store, key)
            bucket["calls"] += 1
            bucket["prompt_tokens"] += int(prompt)
            bucket["completion_tokens"] += int(completion)
            if known:
                bucket["known_cost_usd"] += float(cost)
            else:
                bucket["unknown_cost_count"] += 1
        total_prompt += int(prompt)
        total_completion += int(completion)
        if known:
            known_total += float(cost)
        else:
            unknown_total += 1
    providers = [{"provider": key, **value}
                 for key, value in sorted(by_provider.items())]
    models = []
    for key, value in sorted(by_model.items()):
        provider, _, model = key.partition("::")
        models.append({"provider": provider, "model": model, **value})
    return {
        "total_calls": len(records),
        "bad_lines": bad,
        "known_cost_usd": round(known_total, 6),
        "unknown_cost_count": unknown_total,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "by_provider": providers,
        "by_model": models,
        "note": "记录自 Phase 6 M2 版本开始；此前历史无逐调用记录，"
                "未定价/无响应的调用成本单列为未知，不按 0 计算。",
    }

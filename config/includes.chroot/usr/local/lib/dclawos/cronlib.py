#!/usr/bin/env python3
"""Scheduled agent jobs (Phase 4 module 6, gap row C2).

OpenClaw's cron service (src/cron/service.ts facade, store.ts) is a
51k-LOC subsystem (delivery, recovery, staggering, channel
routing). The portable contract for a single-machine agent is the
job shape + timer semantics implemented here:

  job = {id, schedule, goal, kind ("llm"|"steps"), steps,
         enabled, created_at, last_run, next_run}

Schedules: "every <seconds>" or a standard 5-field cron expression
(minute hour day-of-month month day-of-week; fields support *,
lists, ranges and steps). Jobs persist in
/var/lib/dclawos/cron-jobs.json and are fired by the agent daemon
(fire_due is called from its serve loop); each firing creates a
normal task in the queue, so scheduled work passes the same
approval gates as interactive work.

// reuse-check: OpenClaw cron service NOT copied, reason: most of
  its mass is multi-channel delivery/recovery machinery for a
  hosted gateway; the job-store + schedule + fire contract is what
  transfers, implemented here over agentlib's task files.
"""

import json
import os
import re
import secrets
import time

import agentlib

JOBS_PATH_DEFAULT = "/var/lib/dclawos/cron-jobs.json"
JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def jobs_path():
    return os.environ.get("DCLAWOS_CRON_JOBS", JOBS_PATH_DEFAULT)


def _load():
    try:
        with open(jobs_path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            return [j for j in data if isinstance(j, dict)]
    except (OSError, ValueError):
        pass
    return []


def _save(jobs):
    parent = os.path.dirname(jobs_path())
    if parent:
        agentlib.ensure_exchange_dir(parent)
    agentlib.atomic_write(
        jobs_path(), json.dumps(jobs, indent=2, sort_keys=True) + "\n")


def _field_matches(field, value, minimum, maximum):
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        if "/" in part:
            part, step_text = part.split("/", 1)
            step = int(step_text)
        if part == "*":
            low, high = minimum, maximum
        elif "-" in part:
            low_text, high_text = part.split("-", 1)
            low, high = int(low_text), int(high_text)
        else:
            low = high = int(part)
        if low <= value <= high and (value - low) % step == 0:
            return True
    return False


def cron_matches(expression, when):
    """True when the 5-field cron expression fires at `when`
    (a time.struct_time in local time). Day-of-week: 0/7 = Sunday.
    Standard cron dom/dow OR semantics apply when both are
    restricted."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError(f"cron expression needs 5 fields: {expression!r}")
    minute, hour, dom, month, dow = fields
    if not _field_matches(minute, when.tm_min, 0, 59):
        return False
    if not _field_matches(hour, when.tm_hour, 0, 23):
        return False
    if not _field_matches(month, when.tm_mon, 1, 12):
        return False
    cron_dow = (when.tm_wday + 1) % 7  # python Mon=0 -> cron Sun=0
    dom_hit = _field_matches(dom, when.tm_mday, 1, 31)
    dow_hit = _field_matches(dow, cron_dow, 0, 7) or \
        (cron_dow == 0 and _field_matches(dow, 7, 0, 7))
    dom_restricted = dom != "*"
    dow_restricted = dow != "*"
    if dom_restricted and dow_restricted:
        return dom_hit or dow_hit
    if dom_restricted:
        return dom_hit
    if dow_restricted:
        return dow_hit
    return True


def next_run(schedule, after):
    """Next firing epoch strictly after `after` (seconds)."""
    schedule = (schedule or "").strip()
    if schedule.startswith("every "):
        try:
            interval = int(schedule[6:].strip())
        except ValueError:
            raise ValueError(f"bad interval schedule: {schedule!r}")
        if interval < 5:
            raise ValueError("interval schedule minimum is 5 seconds")
        return after + interval
    # Cron: scan minute by minute up to 4 years.
    candidate = after - (after % 60) + 60
    limit = candidate + 4 * 366 * 24 * 3600
    while candidate <= limit:
        if cron_matches(schedule, time.localtime(candidate)):
            return candidate
        candidate += 60
    raise ValueError(f"cron schedule never fires: {schedule!r}")


def add_job(schedule, goal, kind="llm", steps=None):
    now = int(time.time())
    job = {
        "id": secrets.token_hex(6),
        "schedule": schedule,
        "goal": goal or "",
        "kind": kind if kind in ("llm", "steps") else "llm",
        "steps": steps or [],
        "enabled": True,
        "created_at": now,
        "last_run": None,
        "next_run": next_run(schedule, now),
    }
    jobs = _load()
    jobs.append(job)
    _save(jobs)
    return job


def list_jobs():
    return _load()


def remove_job(job_id):
    jobs = _load()
    kept = [j for j in jobs if j.get("id") != job_id]
    if len(kept) == len(jobs):
        return False
    _save(kept)
    return True


def fire_due(now=None):
    """Create tasks for every enabled job due at `now`.

    Returns the list of created task ids. Called by the daemon's
    serve loop; safe to call repeatedly (a job fires once per
    scheduled minute/interval because next_run advances first).
    """
    now = int(time.time()) if now is None else int(now)
    jobs = _load()
    created = []
    changed = False
    for job in jobs:
        if not job.get("enabled"):
            continue
        due = job.get("next_run")
        if due is None:
            job["next_run"] = next_run(job["schedule"], now)
            changed = True
            continue
        if now >= due:
            task = agentlib.new_task(
                f"cron: {job.get('goal', '')[:60]}",
                job.get("goal", ""), job.get("steps") or [])
            if job.get("kind") == "llm":
                task["kind"] = "llm"
                task["conv_session"] = task["session"]
            agentlib.save_task(task)
            created.append(task["id"])
            job["last_run"] = now
            job["next_run"] = next_run(job["schedule"], now)
            changed = True
            agentlib.log_event(
                "system",
                f"cron job {job['id']} fired task {task['id']}",
                session=task["session"])
    if changed:
        _save(jobs)
    return created

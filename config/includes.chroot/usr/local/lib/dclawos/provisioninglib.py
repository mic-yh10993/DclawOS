#!/usr/bin/env python3
"""Agent self-provisioning (Phase 5, WS2). Standard library only.

The tools behind skill_install / skill_remove / mcp_add /
mcp_remove / channel_manage / registry_list. Design rules (from
the WS2 audit, research/dclawos-reboot/PHASE5-WS2-AUDIT.md):

- Registration writes go through registrylib in-process: the
  registry file is group-writable (dclawos-session, 0660) for
  both the human GUI and this service, so no broker op and no
  service restart are involved — every runtime resolver re-reads
  the registry per call, so installs take effect immediately.
- An install is validated BEFORE it is kept, and any failure
  rolls back completely (staged payload deleted, no registry
  entry): there is no half-installed state.
- Agent installs MUST record provenance + license (stricter than
  the GUI's confirm-on-empty rule): an unattended installer has
  no human in the loop to waive the license check.
- Secrets are never written here. env values in MCP entries may
  reference secret:<name> files a human stored; literal values
  for secret-looking keys are refused (they would sit in the
  group-readable registry in plaintext).
- Self-installed skill payloads live under
  <agent workspace>/skills-store/<id> and nowhere else; removal
  deletes the payload only for entries this module installed.

// reuse-check: OpenClaw's plugin/bundle install machinery NOT
  copied, reason: it is TypeScript bound to the gateway plugin
  loader and marketplace; the DclawOS registry + skills runtime
  already define the local install contract (registrylib entry +
  SKILL.md layout from skills.py), so installation is staged
  file placement + validation + registrylib registration here.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agentlib  # noqa: E402
import registrylib  # noqa: E402

SECRET_KEY_RE = re.compile(r"(key|token|secret|password|passwd)",
                           re.IGNORECASE)
GIT_TIMEOUT = 120
FETCH_TIMEOUT = 60
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


class ProvisionError(Exception):
    pass


def skills_store():
    return os.environ.get(
        "DCLAWOS_SKILLS_STORE",
        os.path.join(agentlib.agent_workspace(), "skills-store"))


def _check_id(entry_id):
    if not isinstance(entry_id, str) or \
            not registrylib.ID_RE.match(entry_id):
        raise ProvisionError(f"invalid id: {entry_id!r}")


def _require_license(params):
    license_text = params.get("license")
    if not isinstance(license_text, str) or not license_text.strip():
        raise ProvisionError(
            "agent installs must record a license (params.license); "
            "there is no human in the loop to waive it")
    return license_text.strip()


def _redact_env(env):
    """Registry env for model eyes: keys + value KIND only."""
    out = {}
    for key, value in (env or {}).items():
        if isinstance(value, str) and value.startswith("secret:"):
            out[key] = value
        else:
            out[key] = "<set>"
    return out


def registry_list(params):
    """Tool: registry_list {kind?} — discovery before use."""
    data, _skipped = registrylib.load_registry()
    kinds = ("skills", "mcp_servers", "connectors", "channels")
    wanted = params.get("kind")
    if wanted and wanted not in kinds:
        raise ProvisionError(f"unknown registry kind: {wanted!r}")
    result = {}
    for kind in kinds:
        if wanted and kind != wanted:
            continue
        entries = []
        for entry in data.get(kind, []):
            shown = dict(entry)
            if "env" in shown:
                shown["env"] = _redact_env(shown.get("env"))
            entries.append(shown)
        result[kind] = entries
    return result


# ------------------------------------------------------------- skills

def _stage_local_dir(source, staging):
    if not os.path.isdir(source):
        raise ProvisionError(f"skill source is not a directory: "
                             f"{source!r}")
    shutil.copytree(source, staging)


def _extract_tar_safely(archive, staging):
    """Member-by-member tar extraction with the PEP 706 'data'
    filter's core guarantees (no path escapes, no links/devices),
    written out by hand: Debian bookworm ships Python 3.11.2,
    whose tarfile has no filter= parameter (the build gate caught
    exactly that — the dev sandbox runs 3.12)."""
    base = os.path.realpath(staging)
    for member in archive.getmembers():
        target = os.path.realpath(os.path.join(base, member.name))
        if target != base and not target.startswith(base + os.sep):
            raise ProvisionError(
                f"archive member escapes the target: "
                f"{member.name!r}")
        if member.isdir():
            os.makedirs(target, exist_ok=True)
        elif member.isfile():
            os.makedirs(os.path.dirname(target), exist_ok=True)
            extracted = archive.extractfile(member)
            with open(target, "wb") as handle:
                shutil.copyfileobj(extracted, handle)
            os.chmod(target, (member.mode & 0o777) or 0o644)
        else:
            raise ProvisionError(
                f"archive member type not allowed: "
                f"{member.name!r}")


def _stage_archive(source, staging):
    if os.path.getsize(source) > MAX_ARCHIVE_BYTES:
        raise ProvisionError("skill archive too large (>64MB)")
    os.makedirs(staging)
    if source.endswith(".zip"):
        with zipfile.ZipFile(source) as archive:
            archive.extractall(staging)
    else:
        with tarfile.open(source) as archive:
            _extract_tar_safely(archive, staging)


def _stage_git(url, staging):
    git = shutil.which("git")
    if git is None:
        raise ProvisionError(
            "git is not available in this image; cannot install "
            "from a git URL")
    try:
        proc = subprocess.run(
            [git, "clone", "--depth", "1", url, staging],
            capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ProvisionError("git clone timed out")
    if proc.returncode != 0:
        raise ProvisionError(
            f"git clone failed: {proc.stderr.strip()[:200]}")


def _stage_url(url, staging):
    import urllib.request
    request = urllib.request.Request(
        url, headers={"User-Agent": "DclawOS-agent/2.0"})
    try:
        with urllib.request.urlopen(request,
                                    timeout=FETCH_TIMEOUT) as resp:
            data = resp.read(MAX_ARCHIVE_BYTES + 1)
    except Exception as exc:  # noqa: BLE001 - network faults are data
        raise ProvisionError(f"skill download failed: {exc}")
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ProvisionError("skill archive too large (>64MB)")
    tmp = staging + ".download"
    with open(tmp, "wb") as handle:
        handle.write(data)
    try:
        _stage_archive(tmp, staging)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _skill_root(staging):
    """Return the directory holding SKILL.md (staging itself, or a
    single wrapping directory inside it)."""
    if os.path.isfile(os.path.join(staging, "SKILL.md")):
        return staging
    children = [os.path.join(staging, name)
                for name in os.listdir(staging)
                if os.path.isdir(os.path.join(staging, name))]
    if len(children) == 1 and os.path.isfile(
            os.path.join(children[0], "SKILL.md")):
        return children[0]
    raise ProvisionError(
        "no SKILL.md at the skill root (or a single wrapping "
        "directory); not a skill package")


def _skill_command(root, params):
    """Resolve how the skill runs: explicit params win; otherwise
    the conventional entry points. A skill with no runnable
    command is NOT installed (the GUI's path-only entries are
    un-runnable — the audit's G-1 lesson; do not repeat it)."""
    command = params.get("command")
    if isinstance(command, str) and command:
        return command, list(params.get("args") or [])
    run_sh = os.path.join(root, "scripts", "run.sh")
    if os.path.isfile(run_sh):
        return "bash", [run_sh]
    skill_py = os.path.join(root, "skill.py")
    if os.path.isfile(skill_py):
        return "python3", [skill_py]
    main_js = os.path.join(root, "index.js")
    if os.path.isfile(main_js):
        return "node", [main_js]
    raise ProvisionError(
        "skill has no runnable command: pass params.command or "
        "ship scripts/run.sh / skill.py / index.js")


def skill_install(params):
    """Tool: skill_install {id, source, license, name?,
    description?, command?, args?, sha256?}.

    source: local directory, local .tar.gz/.tgz/.zip archive,
    http(s) archive URL, or git URL (… .git / git@…)."""
    entry_id = params.get("id")
    _check_id(entry_id)
    license_text = _require_license(params)
    source = params.get("source")
    if not isinstance(source, str) or not source:
        raise ProvisionError("skill_install needs a 'source'")
    store = skills_store()
    os.makedirs(store, exist_ok=True)
    final = os.path.join(store, entry_id)
    if os.path.exists(final):
        raise ProvisionError(
            f"skill already installed at {final}; remove it first")
    staging = os.path.join(
        store, f".staging-{entry_id}-{os.getpid()}")
    registered = False
    try:
        if source.endswith(".git") or source.startswith("git@"):
            _stage_git(source, staging)
        elif source.startswith(("http://", "https://")):
            _stage_url(source, staging)
        elif os.path.isdir(source):
            _stage_local_dir(source, staging)
        elif os.path.isfile(source):
            _stage_archive(source, staging)
        else:
            raise ProvisionError(
                f"skill source not found: {source!r}")
        root = _skill_root(staging)
        if root != staging:
            # Flatten the single wrapping directory into place.
            flattened = staging + ".flat"
            shutil.move(root, flattened)
            shutil.rmtree(staging)
            shutil.move(flattened, staging)
        command, args = _skill_command(staging, params)
        description = params.get("description") or ""
        if not description:
            import skills as skills_mod
            description = skills_mod.describe_skill(
                {"id": entry_id, "path": staging})
        # Land the payload at its final home BEFORE registering,
        # so the registry never points at a staging path.
        shutil.move(staging, final)
        args = [a.replace(staging, final) for a in args]
        entry = {
            "id": entry_id,
            "name": params.get("name") or entry_id,
            "path": final,
            "command": command,
            "args": args,
            "env": {},
            "description": description,
            "license": license_text,
            "enabled": True,
            "provenance": {
                "source": source,
                "installed_by": "agent",
                "installed_at": int(time.time()),
            },
        }
        data, _skipped = registrylib.load_registry()
        registrylib.add_skill(data, entry)
        registrylib.save_registry(data)
        registered = True
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if not registered:
            shutil.rmtree(final, ignore_errors=True)
        raise
    agentlib.log_event(
        "system",
        f"skill installed by agent: {entry_id} from {source} "
        f"(license {license_text})")
    return {"id": entry_id, "path": final, "command": command,
            "args": args, "license": license_text}


def skill_remove(params):
    """Tool: skill_remove {id}. Registry entry always removed;
    the payload directory is deleted only for agent-installed
    skills whose path is inside the skills store."""
    entry_id = params.get("id")
    _check_id(entry_id)
    data, _skipped = registrylib.load_registry()
    entry = None
    for candidate in data.get("skills", []):
        if candidate.get("id") == entry_id:
            entry = candidate
            break
    if entry is None:
        raise ProvisionError(f"unknown skill: {entry_id!r}")
    payload_deleted = False
    path = entry.get("path") or ""
    provenance = entry.get("provenance") or {}
    store = os.path.realpath(skills_store())
    if provenance.get("installed_by") == "agent" and \
            os.path.realpath(path).startswith(store + "/"):
        shutil.rmtree(path, ignore_errors=True)
        payload_deleted = True
    registrylib.remove_entry(data, "skills", entry_id)
    registrylib.save_registry(data)
    agentlib.log_event(
        "system", f"skill removed by agent: {entry_id} "
                  f"(payload deleted: {payload_deleted})")
    return {"removed": entry_id, "payload_deleted": payload_deleted}


# ---------------------------------------------------------------- mcp

def _check_env(env):
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise ProvisionError("env must be an object")
    for key, value in env.items():
        if not isinstance(value, str):
            raise ProvisionError("env values must be strings")
        if SECRET_KEY_RE.search(str(key)) and \
                not value.startswith("secret:"):
            raise ProvisionError(
                f"env {key} looks secret-bearing: store the value "
                "as a secret (human, Settings → 密钥) and reference "
                "it as secret:<name>; literal secrets must not sit "
                "in the registry")
    return env


def mcp_add(params):
    """Tool: mcp_add {id, name?, command?, args?, url?, env?,
    license, description?}. The server must answer a live MCP
    handshake (initialize + tools/list) before the entry is kept;
    a failed validation registers NOTHING."""
    import mcpclient
    entry_id = params.get("id")
    _check_id(entry_id)
    license_text = _require_license(params)
    url = params.get("url")
    command = params.get("command")
    entry = {
        "id": entry_id,
        "name": params.get("name") or entry_id,
        "description": params.get("description") or "",
        "license": license_text,
        "enabled": True,
        "env": _check_env(params.get("env")),
        "provenance": {
            "installed_by": "agent",
            "installed_at": int(time.time()),
        },
    }
    if isinstance(url, str) and url:
        if not url.startswith(("http://", "https://")):
            raise ProvisionError("mcp url must be http(s)")
        entry["url"] = url
        entry["transport"] = "http"
        entry["command"] = ""
        entry["args"] = []
    elif isinstance(command, str) and command:
        entry["command"] = command
        args = params.get("args") or []
        if not isinstance(args, list) or not all(
                isinstance(a, str) for a in args):
            raise ProvisionError("args must be a list of strings")
        entry["args"] = args
        entry["transport"] = "stdio"
    else:
        raise ProvisionError(
            "mcp_add needs a stdio 'command' or an http(s) 'url'")
    # Validate BEFORE registering (rollback = never registered).
    tools = mcpclient.validate_server(entry)
    data, _skipped = registrylib.load_registry()
    registrylib.add_server(data, entry)
    registrylib.save_registry(data)
    agentlib.log_event(
        "system",
        f"mcp server added by agent: {entry_id} "
        f"({entry['transport']}, {len(tools)} tools, "
        f"license {license_text})")
    return {"id": entry_id, "transport": entry["transport"],
            "tools": tools}


def mcp_remove(params):
    """Tool: mcp_remove {id}."""
    entry_id = params.get("id")
    _check_id(entry_id)
    data, _skipped = registrylib.load_registry()
    registrylib.remove_entry(data, "mcp_servers", entry_id)
    registrylib.save_registry(data)
    agentlib.log_event(
        "system", f"mcp server removed by agent: {entry_id}")
    return {"removed": entry_id}


# ------------------------------------------------------------ channels

def channel_manage(params):
    """Tool: channel_manage {action, channel, kind?, name?,
    allowlist?}. Actions: list | add | remove | enable | disable |
    set_allowlist. Tokens are NEVER set here: a channel's bot
    token is a human-written secret (Settings → 渠道); add records
    the conventional secret name channel-<id> and reports whether
    that secret exists yet."""
    action = params.get("action")
    if action == "list":
        data, _skipped = registrylib.load_registry()
        out = []
        for entry in data.get("channels", []):
            shown = dict(entry)
            secret_name = entry.get("secret") or ""
            shown["token_configured"] = bool(secret_name) and \
                os.path.exists(os.path.join(
                    agentlib.secrets_dir(), secret_name))
            out.append(shown)
        return {"channels": out}
    channel_id = params.get("channel")
    _check_id(channel_id)
    data, _skipped = registrylib.load_registry()
    if action == "add":
        kind = params.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ProvisionError("channel add needs a 'kind'")
        entry = {
            "id": channel_id,
            "name": params.get("name") or channel_id,
            "kind": kind,
            "allowlist": [str(a) for a in
                          params.get("allowlist") or []],
            "secret": f"channel-{channel_id}",
            "enabled": True,
            "description": params.get("description") or "",
            "license": params.get("license") or "",
            "options": params.get("options") or {},
            "provenance": {
                "installed_by": "agent",
                "installed_at": int(time.time()),
            },
        }
        added = registrylib.add_channel(data, entry)
        registrylib.save_registry(data)
        token_ready = os.path.exists(os.path.join(
            agentlib.secrets_dir(), added["secret"]))
        agentlib.log_event(
            "system", f"channel added by agent: {channel_id} "
                      f"(kind {kind})")
        return {"id": channel_id, "kind": kind,
                "token_configured": token_ready,
                "note": "a human must save the bot token in "
                        "Settings → 渠道 before this channel can "
                        "connect" if not token_ready else "ready"}
    entry = None
    for candidate in data.get("channels", []):
        if candidate.get("id") == channel_id:
            entry = candidate
            break
    if entry is None:
        raise ProvisionError(f"unknown channel: {channel_id!r}")
    if action == "remove":
        registrylib.remove_entry(data, "channels", channel_id)
    elif action in ("enable", "disable"):
        registrylib.set_enabled(data, "channels", channel_id,
                                action == "enable")
    elif action == "set_allowlist":
        allowlist = params.get("allowlist")
        if not isinstance(allowlist, list):
            raise ProvisionError("set_allowlist needs a list")
        entry["allowlist"] = [str(a) for a in allowlist]
    else:
        raise ProvisionError(
            f"unknown channel_manage action: {action!r}")
    registrylib.save_registry(data)
    agentlib.log_event(
        "system", f"channel {action} by agent: {channel_id}")
    return {"channel": channel_id, "action": action}

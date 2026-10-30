#!/usr/bin/env python3
"""Self-provisioning selftest (Phase 5 module 4, build gate).

Exercises provisioninglib the way the daemon's execute_step does
(after the approval gate, which earlier phases already cover):
skill_install from a local directory and from a tar.gz archive,
license enforcement, rollback on failure, skill_remove payload
deletion, mcp_add with a live handshake against a tiny stdio MCP
server AND a mock streamable-HTTP MCP server, rollback when the
handshake fails, the env secret guard, channel_manage lifecycle,
registry_list env redaction, and the classify() tiers for the new
tools (incl. the shell-delete tier-2 guard). Local fixtures only:
no network, no real credentials.

Exits non-zero on the first failed assertion. Invoked by build
hook 1008 inside the image chroot; also runnable by hand (it only
touches a temp dir).
"""

import io
import json
import os
import sys
import tarfile
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/usr/local/lib/dclawos")

ROOT = tempfile.mkdtemp(prefix="dclawos-provtest-")
os.environ["DCLAWOS_TASKS_DIR"] = os.path.join(ROOT, "tasks")
os.environ["DCLAWOS_EVENTS_PATH"] = os.path.join(ROOT, "events.jsonl")
os.environ["DCLAWOS_SECRETS_DIR"] = os.path.join(ROOT, "secrets")
os.environ["DCLAWOS_STOP_FLAG"] = os.path.join(ROOT, "stop.json")
os.environ["DCLAWOS_AGENT_WORKSPACE"] = os.path.join(ROOT, "workspace")
os.environ["DCLAWOS_SKILLS_STORE"] = os.path.join(ROOT, "store")
os.environ["DCLAWOS_REGISTRY"] = os.path.join(ROOT, "registry.json")
os.makedirs(os.environ["DCLAWOS_AGENT_WORKSPACE"], exist_ok=True)
os.makedirs(os.environ["DCLAWOS_SECRETS_DIR"], exist_ok=True)

import agentlib  # noqa: E402
import mcpclient  # noqa: E402
import provisioninglib  # noqa: E402
import registrylib  # noqa: E402
import skills as skills_mod  # noqa: E402
import toolslib  # noqa: E402


def fail(message):
    print(f"provisiontest FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def expect_error(fn, needle):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - assertion helper
        if needle and needle not in str(exc):
            fail(f"error {exc!r} does not mention {needle!r}")
        return
    fail(f"expected an error mentioning {needle!r}, got success")


# ------------------------------------------------------------ tiers
if set(toolslib.SCHEMA_NAMES) != set(agentlib.TOOLS):
    fail(f"tool schema/dispatch drift: "
         f"{set(toolslib.SCHEMA_NAMES) ^ set(agentlib.TOOLS)}")
for tool, wanted in (("registry_list", 0), ("skill_install", 2),
                     ("skill_remove", 2), ("mcp_add", 2),
                     ("mcp_remove", 2)):
    tier, _reason = agentlib.classify(tool, {})
    if tier != wanted:
        fail(f"classify {tool} = tier {tier}, want {wanted}")
tier, _ = agentlib.classify("channel_manage", {"action": "list"})
if tier != 0:
    fail("channel_manage list must be tier 0")
tier, _ = agentlib.classify("channel_manage", {"action": "enable"})
if tier != 2:
    fail("channel_manage enable must be tier 2")
tier, _ = agentlib.classify("shell", {"argv": ["rm", "-rf", "x"]})
if tier != 2:
    fail("shell rm must be tier 2 (audit G-21)")
tier, _ = agentlib.classify("shell", {"argv": ["ls"]})
if tier != 1:
    fail("plain shell must stay tier 1")

# ------------------------------------------------------- skill flow
fixture = os.path.join(ROOT, "fixture-skill")
os.makedirs(os.path.join(fixture, "scripts"))
with open(os.path.join(fixture, "SKILL.md"), "w",
          encoding="utf-8") as handle:
    handle.write("# Echo skill\nRepeats its input.\n")
with open(os.path.join(fixture, "scripts", "run.sh"), "w",
          encoding="utf-8") as handle:
    handle.write("#!/bin/sh\nprintf 'skill-echo:'\ncat\n")
os.chmod(os.path.join(fixture, "scripts", "run.sh"), 0o755)

expect_error(
    lambda: provisioninglib.skill_install(
        {"id": "echo", "source": fixture}),
    "license")
data, _ = registrylib.load_registry()
if data["skills"]:
    fail("license-less install left a registry entry")

installed = provisioninglib.skill_install(
    {"id": "echo", "source": fixture, "license": "MIT"})
if not installed["path"].startswith(
        os.environ["DCLAWOS_SKILLS_STORE"]):
    fail(f"payload not in the skills store: {installed}")
ran = skills_mod.run_skill({"skill": "echo", "input": "hello"}, None)
if "skill-echo:hello" not in ran["stdout"]:
    fail(f"installed skill does not run: {ran}")

expect_error(
    lambda: provisioninglib.skill_install(
        {"id": "echo", "source": fixture, "license": "MIT"}),
    "already installed")

# Archive form (single wrapping directory must flatten).
archive_path = os.path.join(ROOT, "packed.tar.gz")
with tarfile.open(archive_path, "w:gz") as archive:
    for name in ("SKILL.md", os.path.join("scripts", "run.sh")):
        archive.add(os.path.join(fixture, name),
                    arcname=os.path.join("packed-skill", name))
installed2 = provisioninglib.skill_install(
    {"id": "packed", "source": archive_path, "license": "BSD-2"})
if not os.path.isfile(os.path.join(installed2["path"], "SKILL.md")):
    fail("archive install did not flatten to the skill root")

removed = provisioninglib.skill_remove({"id": "packed"})
if not removed["payload_deleted"] or \
        os.path.exists(installed2["path"]):
    fail(f"skill_remove did not delete the payload: {removed}")
data, _ = registrylib.load_registry()
if [s["id"] for s in data["skills"]] != ["echo"]:
    fail(f"registry skills wrong after remove: {data['skills']}")

# --------------------------------------------------------- mcp flow
server_py = os.path.join(ROOT, "tiny_mcp.py")
with open(server_py, "w", encoding="utf-8") as handle:
    handle.write(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    req = json.loads(line)\n"
        "    method = req.get('method')\n"
        "    if method == 'initialize':\n"
        "        result = {'protocolVersion': '2024-11-05',\n"
        "                  'capabilities': {}, 'serverInfo': "
        "{'name': 'tiny', 'version': '1'}}\n"
        "    elif method == 'tools/list':\n"
        "        result = {'tools': [{'name': 'echo', "
        "'description': 'echo', 'inputSchema': "
        "{'type': 'object'}}]}\n"
        "    elif method == 'tools/call':\n"
        "        args = (req.get('params') or {}).get("
        "'arguments') or {}\n"
        "        result = {'content': [{'type': 'text', 'text': "
        "'echo:' + str(args.get('text', ''))}]}\n"
        "    else:\n"
        "        continue\n"
        "    sys.stdout.write(json.dumps({'jsonrpc': '2.0', "
        "'id': req.get('id'), 'result': result}) + '\\n')\n"
        "    sys.stdout.flush()\n")

expect_error(
    lambda: provisioninglib.mcp_add(
        {"id": "badenv", "license": "MIT", "command": "python3",
         "args": [server_py], "env": {"API_TOKEN": "literal"}}),
    "secret:")
expect_error(
    lambda: provisioninglib.mcp_add(
        {"id": "ghost", "license": "MIT",
         "command": "/nonexistent/mcp-binary"}),
    "")
data, _ = registrylib.load_registry()
if data["mcp_servers"]:
    fail("failed mcp_add attempts left registry entries")

added = provisioninglib.mcp_add(
    {"id": "tiny", "license": "MIT", "command": "python3",
     "args": [server_py]})
if added["tools"] != ["echo"]:
    fail(f"mcp_add handshake tools wrong: {added}")
called = mcpclient.call_tool(
    {"server": "tiny", "tool": "echo",
     "arguments": {"text": "via-stdio"}}, None)
content = (called["result"].get("content") or [{}])[0].get("text")
if content != "echo:via-stdio":
    fail(f"mcp_call over stdio failed: {called}")


class MockMCPHttp(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        method = body.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05",
                      "capabilities": {},
                      "serverInfo": {"name": "mockhttp",
                                     "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "echo",
                                 "description": "echo",
                                 "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            args = (body.get("params") or {}).get("arguments") or {}
            result = {"content": [{"type": "text",
                                   "text": "http-echo:" +
                                   str(args.get("text", ""))}]}
        else:
            raw = b""
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.wfile.write(raw)
            return
        raw = json.dumps({"jsonrpc": "2.0", "id": body.get("id"),
                          "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "sess-1")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


http_server = HTTPServer(("127.0.0.1", 0), MockMCPHttp)
threading.Thread(target=http_server.serve_forever,
                 daemon=True).start()
added_http = provisioninglib.mcp_add(
    {"id": "tinyhttp", "license": "MIT",
     "url": f"http://127.0.0.1:{http_server.server_address[1]}/mcp"})
if added_http["transport"] != "http" or \
        added_http["tools"] != ["echo"]:
    fail(f"mcp_add http handshake wrong: {added_http}")
called_http = mcpclient.call_tool(
    {"server": "tinyhttp", "tool": "echo",
     "arguments": {"text": "via-http"}}, None)
content = (called_http["result"].get("content") or [{}])[0].get(
    "text")
if content != "http-echo:via-http":
    fail(f"mcp_call over http failed: {called_http}")
http_server.shutdown()
provisioninglib.mcp_remove({"id": "tinyhttp"})
provisioninglib.mcp_remove({"id": "tiny"})
data, _ = registrylib.load_registry()
if data["mcp_servers"]:
    fail("mcp_remove left entries behind")

# -------------------------------------------------- channel + redact
managed = provisioninglib.channel_manage(
    {"action": "add", "channel": "tgmain", "kind": "telegram",
     "allowlist": ["42"]})
if managed["token_configured"]:
    fail("channel token must start unconfigured")
listed = provisioninglib.channel_manage({"action": "list"})
row = [c for c in listed["channels"] if c["id"] == "tgmain"]
if len(row) != 1 or row[0]["secret"] != "channel-tgmain":
    fail(f"channel_manage list wrong: {listed}")
with open(os.path.join(os.environ["DCLAWOS_SECRETS_DIR"],
                       "channel-tgmain"), "w",
          encoding="utf-8") as handle:
    handle.write("dummy\n")
listed = provisioninglib.channel_manage({"action": "list"})
row = [c for c in listed["channels"] if c["id"] == "tgmain"][0]
if not row["token_configured"]:
    fail("channel token_configured did not flip after secret write")
provisioninglib.channel_manage(
    {"action": "set_allowlist", "channel": "tgmain",
     "allowlist": ["42", "43"]})
provisioninglib.channel_manage(
    {"action": "remove", "channel": "tgmain"})
data, _ = registrylib.load_registry()
if data["channels"]:
    fail("channel_manage remove left the entry behind")

data, _ = registrylib.load_registry()
registrylib.add_server(data, {
    "id": "redact", "name": "redact", "command": "true",
    "env": {"API_TOKEN": "literal-secret-value",
            "REF": "secret:some-name"}})
registrylib.save_registry(data)
shown = provisioninglib.registry_list({"kind": "mcp_servers"})
env = shown["mcp_servers"][0]["env"]
if env.get("API_TOKEN") != "<set>" or \
        env.get("REF") != "secret:some-name":
    fail(f"registry_list redaction wrong: {env}")
if "literal-secret-value" in json.dumps(shown):
    fail("registry_list leaked a literal env value")

print("provisiontest OK: tiers, skill install/remove with "
      "rollback, MCP handshake-gated add (stdio + HTTP), channel "
      "management and registry redaction verified")

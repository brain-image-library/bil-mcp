"""Lesson 2: the three MCP primitives (tools, resources, prompts) on the real server.

Run from the repo root:   uv run python learn/02_primitives.py

Same by-hand approach as lesson 1: raw JSON-RPC over the server's stdin and
stdout. Each section asks the server for one primitive. This script cuts long
values to keep the output readable; the cut points are marked.
"""

import json
import subprocess
import sys

server = subprocess.Popen([sys.executable, "-m", "bil_mcp"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
_next_id = 0


def rpc(method: str, params: dict | None = None) -> dict:
    """Send one request, return its 'result'."""
    global _next_id
    _next_id += 1
    msg = {"jsonrpc": "2.0", "id": _next_id, "method": method}
    if params is not None:
        msg["params"] = params
    print(f"\n>>> {method} {json.dumps(params) if params else ''}")
    server.stdin.write(json.dumps(msg) + "\n")
    server.stdin.flush()
    return json.loads(server.stdout.readline())["result"]


def cut(s: str, n: int = 160) -> str:
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[:n] + f"... [cut, {len(s)} chars]"


rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "lesson2", "version": "0"}})
server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
server.stdin.flush()

print("\n========== TOOLS: actions the MODEL chooses to call ==========")
for t in rpc("tools/list")["tools"]:
    print(f"  {t['name']:24} {cut(t['description'].splitlines()[0], 80)}")

print("\n========== RESOURCES: read-only data the APPLICATION attaches ==========")
for r in rpc("resources/list")["resources"]:
    print(f"  fixed URI:    {r['uri']:28} {r.get('description')}")
for r in rpc("resources/templates/list")["resourceTemplates"]:
    print(f"  URI template: {r['uriTemplate']:28} {r.get('description')}")
res = rpc("resources/read", {"uri": "bil://dataset/rut"})
print("  contents[0].mimeType =", res["contents"][0]["mimeType"])
print("  contents[0].text     =", cut(res["contents"][0]["text"]))

print("\n========== PROMPTS: templates the USER picks ==========")
for p in rpc("prompts/list")["prompts"]:
    args = [a["name"] for a in p.get("arguments", [])]
    print(f"  {p['name']:16} args={args}  {p.get('description')}")
got = rpc("prompts/get", {"name": "find_datasets",
                          "arguments": {"question": "Is there human MERFISH data?"}})
m = got["messages"][0]
print(f"  messages[0].role = {m['role']}")
print(f"  messages[0].content.text = {cut(m['content']['text'], 220)}")

server.stdin.close()
server.wait()

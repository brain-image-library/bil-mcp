"""Lesson 3: how a Python function becomes an MCP tool.

Run from the repo root:   uv run python learn/03_schemas.py

Part A prints the real Python source of the `search_datasets` tool.
Part B asks the running server (raw JSON-RPC, as in lessons 1-2) for that
tool's definition, and prints its input schema in full and its output
schema's top-level fields. Compare the two.
Part C sends arguments that break the schema and shows who rejects them.
"""

import inspect
import json
import subprocess
import sys

import bil_mcp.server as srv

print("========== A. The Python function (src/bil_mcp/server.py) ==========")
src = inspect.getsource(srv.search_datasets)
print(src[: src.index('    try:')])  # decorator, signature and docstring; the body is in the file

server = subprocess.Popen([sys.executable, "-m", "bil_mcp"], stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
_id = 0


def rpc(method, params=None):
    global _id
    _id += 1
    msg = {"jsonrpc": "2.0", "id": _id, "method": method, **({"params": params} if params else {})}
    server.stdin.write(json.dumps(msg) + "\n")
    server.stdin.flush()
    return json.loads(server.stdout.readline())


rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "lesson3", "version": "0"}})
server.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
server.stdin.flush()

tool = next(t for t in rpc("tools/list")["result"]["tools"] if t["name"] == "search_datasets")
print("========== B. What tools/list sends for it ==========")
print("name        :", tool["name"])
print("description :", json.dumps(tool["description"]))
print("annotations :", json.dumps(tool["annotations"]))
print("inputSchema :")
print(json.dumps(tool["inputSchema"], indent=2))
out = tool["outputSchema"]
print("outputSchema: properties =", list(out["properties"]), " required =", out.get("required"))

print("\n========== C. Arguments that break the schema ==========")
for args in [
    {"filters": {"species": "Human"}, "limit": 1000},          # limit has le=100
    {"filters": {"species": "Human"}, "match": "fuzzy"},       # match is Literal["contains","exact"]
    {"filters": "species=Human"},                               # filters must be an object
]:
    r = rpc("tools/call", {"name": "search_datasets", "arguments": args})["result"]
    print(f"\nargs    = {json.dumps(args)}")
    print(f"isError = {r.get('isError')}")
    print("text    =", r["content"][0]["text"].replace("\n", " | ")[:300])

server.stdin.close()
server.wait()

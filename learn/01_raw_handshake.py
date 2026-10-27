"""Lesson 1: talk to the BIL MCP server by hand, with no MCP library.

Run from the repo root:   uv run python learn/01_raw_handshake.py

This does what Claude Desktop does: start the server as a subprocess, then
write JSON messages to its stdin and read JSON replies from its stdout, one
message per line. Each message is printed so you can see the whole
conversation. Long replies are cut to 300 characters by this script.
"""

import json
import subprocess
import sys

server = subprocess.Popen(
    [sys.executable, "-m", "bil_mcp"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
)


def send(msg: dict) -> None:
    line = json.dumps(msg)
    print(f"\n>>> CLIENT SENDS\n{line}")
    server.stdin.write(line + "\n")
    server.stdin.flush()


def receive() -> dict:
    line = server.stdout.readline()
    shown = line if len(line) <= 300 else line[:300] + f"... [{len(line)} chars total]"
    print(f"<<< SERVER REPLIES\n{shown.rstrip()}")
    return json.loads(line)


# 1. Handshake: the client introduces itself and proposes a protocol version.
send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
      "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                 "clientInfo": {"name": "kasia-by-hand", "version": "0"}}})
receive()
# 2. Client confirms. This is a *notification*: it has no "id", so there's no reply.
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

# 3. Ask what tools exist.
send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
tools = receive()["result"]["tools"]
print("\n    tool names:", [t["name"] for t in tools])

# 4. Call one tool, the way a model would after choosing it.
send({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
      "params": {"name": "search_datasets",
                 "arguments": {"filters": {"species": "Humanh", "technique": "MERFISH"}, "limit": 1}}})
result = receive()["result"]
print("\n    structuredContent.total =", result["structuredContent"]["total"])
sc = result["structuredContent"]
if sc["datasets"]:
    print("    first dataset title    =", sc["datasets"][0]["title"])
print("    notes                  =", sc["notes"])
print("    isError                =", result.get("isError"))

server.stdin.close()
server.wait()

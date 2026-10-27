"""Lesson 6: server plumbing - shared state, errors, and why stdout is sacred.

Run from the repo root:   uv run python learn/06_plumbing.py

Each section starts the real server as a subprocess (as Claude Desktop does)
and talks raw JSON-RPC to it, as in lessons 1-3. The server's stderr (its log)
goes to a file so we can read it between calls.
"""

import json
import os
import subprocess
import sys
import tempfile


class Server:
    def __init__(self, cmd: list[str], env: dict | None = None):
        self.log = tempfile.NamedTemporaryFile("w+", suffix=".log", delete=False)
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
                                  text=True, env={**os.environ, **(env or {})})
        self.id = 0

    def send(self, msg: dict) -> None:
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self.id += 1
        self.send({"jsonrpc": "2.0", "id": self.id, "method": method, **({"params": params} if params else {})})
        return json.loads(self.p.stdout.readline())

    def handshake(self) -> None:
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "lesson6", "version": "0"}})
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def log_lines(self) -> list[str]:
        self.log.flush()
        return open(self.log.name).read().splitlines()

    def close(self) -> None:
        self.p.stdin.close()
        self.p.wait()


SEARCH = {"name": "search_datasets", "arguments": {"filters": {"species": "Human", "technique": "MERFISH"}, "limit": 1}}

print("========== A. Lifespan: one client and one cache for the whole process ==========")
s = Server([sys.executable, "-m", "bil_mcp", "--log-level", "DEBUG"])
s.handshake()
for n in (1, 2):
    before = len([l for l in s.log_lines() if "BIL request" in l])
    total = s.rpc("tools/call", SEARCH)["result"]["structuredContent"]["total"]
    after = len([l for l in s.log_lines() if "BIL request" in l])
    print(f"  tools/call #{n}: total={total}, HTTP requests to BIL during this call: {after - before}")
print("  'server starting' lines in the log:", len([l for l in s.log_lines() if "starting" in l]))
s.close()

print("\n========== B. BIL unreachable: a tool error the model can read ==========")
s = Server([sys.executable, "-m", "bil_mcp"],
           env={"BIL_API_BASE": "https://api.brainimagelibrary.invalid", "BIL_MAX_RETRIES": "0"})
s.handshake()
r = s.rpc("tools/call", SEARCH)["result"]
print("  isError:", r["isError"])
print("  text   :", r["content"][0]["text"])
s.close()

print("\n========== C. stdout is the protocol channel: where does a print() go? ==========")
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "x", "version": "0"}}}
cases = {
    "print before the server starts":
        "print('debug: hello', flush=True); from bil_mcp.server import main; main()",
    "print inside a tool, while serving":
        "import bil_mcp.server as srv\n"
        "@srv.mcp.tool()\n"
        "def noisy() -> str:\n"
        "    print('debug: hello', flush=True)\n"
        "    return 'ok'\n"
        "srv.main()",
}
for label, code in cases.items():
    s = Server([sys.executable, "-c", code])
    s.send(INIT)
    first = s.p.stdout.readline()
    s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    s.send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "noisy", "arguments": {}}})
    s.close()
    try:
        json.loads(first)
        verdict = "valid JSON-RPC"
    except json.JSONDecodeError:
        verdict = "NOT JSON -> the client cannot parse it"
    in_log = any("debug: hello" in l for l in s.log_lines())
    print(f"  {label}:")
    print(f"    first stdout line : {first[:45]!r}... ({verdict})")
    print(f"    'debug: hello' ended up in stderr log: {in_log}")

print("\n========== D. Tool description after the docstring clean-up ==========")
s = Server([sys.executable, "-m", "bil_mcp"])
s.handshake()
tool = next(t for t in s.rpc("tools/list")["result"]["tools"] if t["name"] == "search_datasets")
print("  ", json.dumps(tool["description"]))
s.close()

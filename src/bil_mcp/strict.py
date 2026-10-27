"""Reject tool arguments the tool doesn't have.

By default the SDK validates arguments with Pydantic, which silently DROPS
unknown keys: a model that writes {"max_results": 50} instead of
{"limit": 50} gets the default 10 results and no warning. The SDK (mcp 2.3)
has no setting to change that, so this module adds it through the SDK's
public middleware hook (`MCPServer.middleware`).

Middleware is code that wraps every incoming message: it sees the raw request
before the SDK handles it, and the result after. This one does two things:

* on `tools/call`: if any argument name is not in the tool's input schema,
  return a TOOL error (isError=true) naming it and suggesting close matches,
  so the model can fix its call. The tool itself never runs.
* on `tools/list`: add "additionalProperties": false to every input schema,
  so the model is told up front that extra keys are not allowed.
"""

from __future__ import annotations

import difflib
from typing import Any

from mcp.types import CallToolResult, TextContent


class RejectUnknownArguments:
    def __init__(self, server: Any):
        self.server = server
        self._allowed: dict[str, set[str]] | None = None

    async def _allowed_args(self) -> dict[str, set[str]]:
        if self._allowed is None:  # tools are fixed once the server runs, so compute once
            tools = await self.server.list_tools()
            self._allowed = {t.name: set((t.input_schema or {}).get("properties", {})) for t in tools}
        return self._allowed

    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        params = ctx.params if isinstance(ctx.params, dict) else {}
        if ctx.method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            allowed = (await self._allowed_args()).get(name)
            if allowed is not None and isinstance(args, dict):
                unknown = sorted(set(args) - allowed)
                if unknown:
                    return CallToolResult(content=[TextContent(type="text", text=_message(name, unknown, allowed))],
                                          is_error=True)
        result = await call_next(ctx)
        if ctx.method == "tools/list":
            # At this point the result is already the wire-format dict: {"tools": [{"inputSchema": ...}]}
            tools = result.get("tools", []) if isinstance(result, dict) else getattr(result, "tools", [])
            for tool in tools or []:
                schema = tool.get("inputSchema") if isinstance(tool, dict) else tool.input_schema
                if isinstance(schema, dict):
                    schema["additionalProperties"] = False
        return result


def _message(tool: str, unknown: list[str], allowed: set[str]) -> str:
    parts = []
    for u in unknown:
        close = difflib.get_close_matches(u, sorted(allowed), n=1, cutoff=0.5)
        parts.append(f"'{u}'" + (f" (did you mean '{close[0]}'?)" if close else ""))
    return (f"Unknown argument(s) for {tool}: {', '.join(parts)}. "
            f"Valid arguments: {', '.join(sorted(allowed)) or '(none)'}.")

"""Log every tool call: which tool, the exact arguments the model sent, and the outcome.

Claude Desktop's own log records only `method="tools/call" id=4 params` (the
arguments are elided), and the client's DEBUG lines show the HTTP requests to
BIL, not the request the model made. This middleware closes that gap:

    bil_mcp.audit INFO tool call #4 search_datasets {"text": "xenium", "limit": 30}
    bil_mcp.audit INFO tool done #4 search_datasets ok in 0.12 s

Logged at INFO, so `--log-level INFO` shows the conversation without the
per-request HTTP detail of DEBUG.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger("bil_mcp.audit")


class LogToolCalls:
    async def __call__(self, ctx: Any, call_next: Any) -> Any:
        if ctx.method != "tools/call" or not isinstance(ctx.params, dict):
            return await call_next(ctx)
        name = ctx.params.get("name")
        rid = getattr(ctx, "request_id", None)
        args = json.dumps(ctx.params.get("arguments") or {}, ensure_ascii=False)
        log.info("tool call #%s %s %s", rid, name, args)
        t0 = time.perf_counter()
        try:
            result = await call_next(ctx)
        except Exception as e:
            log.info("tool done #%s %s PROTOCOL ERROR %s in %.2f s", rid, name, e, time.perf_counter() - t0)
            raise
        is_error = result.get("isError") if isinstance(result, dict) else getattr(result, "is_error", None)
        log.info("tool done #%s %s %s in %.2f s", rid, name, "ERROR" if is_error else "ok",
                 time.perf_counter() - t0)
        return result

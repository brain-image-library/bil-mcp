"""Is a newer bil-mcp release available? Never blocks the server and never raises.

Call once at startup (in the lifespan function) and keep the result. The first tool result of the
session carries it as a notice for the agent to pass on.

Until bil-mcp is published on PyPI this returns None (PyPI answers 404), so it is safe to ship now.
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

import httpx
from packaging.version import InvalidVersion, Version

PACKAGE = "bil-mcp"


async def update_notice(client: httpx.AsyncClient | None = None, timeout: float = 2.0) -> str | None:
    """A one-line notice if PyPI has a newer release, else None (also None offline or on any error)."""
    try:
        installed = Version(version(PACKAGE))
    except (PackageNotFoundError, InvalidVersion):
        return None                                  # running from a source checkout
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        r = await client.get(f"https://pypi.org/pypi/{PACKAGE}/json", timeout=timeout)
        if r.status_code != 200:
            return None
        latest = Version(r.json()["info"]["version"])
    except Exception:                                # offline, slow, or unexpected JSON: stay quiet
        return None
    finally:
        if own:
            await client.aclose()
    if latest <= installed:
        return None
    return (f"bil-mcp {installed} is installed; {latest} is available. Results may miss fixes. "
            f"Update with: uvx bil-mcp@latest (or: uv tool upgrade bil-mcp).")

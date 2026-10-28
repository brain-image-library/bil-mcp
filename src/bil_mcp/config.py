"""Runtime settings, read once from environment variables.

Every knob has a safe default, so the server works with zero configuration.
MCP clients (Claude Desktop, Claude Code, ...) can override these through the
``env`` block of their server config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    api_base: str = "https://api.brainimagelibrary.org"
    download_base: str = "https://download.brainimagelibrary.org"
    viewer_base: str = "https://api.brainimagelibrary.org/web/view"
    timeout_s: float = 60.0
    download_timeout_s: float = 15.0   # directory pages are small; fail fast if the server is down
    status_url: str = "https://www.brainimagelibrary.org/datacentermove.html"
    cache_ttl_s: float = 3600.0
    cache_max_entries: int = 50_000
    retrieve_batch_size: int = 200
    max_concurrency: int = 4
    max_retries: int = 2
    # Above this many matches, tools don't fetch records unless asked (fetch_all=true).
    # 200 records per POST, 4 POSTs at a time, 0.3-1 s per round: 2000 records = 10 POSTs in
    # 3 rounds, measured under 5 s. Tool descriptions read this value (server.STATS_LIMIT).
    stats_limit: int = 2000

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            api_base=os.environ.get("BIL_API_BASE", d.api_base).rstrip("/"),
            download_base=os.environ.get("BIL_DOWNLOAD_BASE", d.download_base).rstrip("/"),
            viewer_base=os.environ.get("BIL_VIEWER_BASE", d.viewer_base),
            timeout_s=_float("BIL_TIMEOUT", d.timeout_s),
            download_timeout_s=_float("BIL_DOWNLOAD_TIMEOUT", d.download_timeout_s),
            status_url=os.environ.get("BIL_STATUS_URL", d.status_url),
            cache_ttl_s=_float("BIL_CACHE_TTL", d.cache_ttl_s),
            cache_max_entries=_int("BIL_CACHE_MAX_ENTRIES", d.cache_max_entries),
            retrieve_batch_size=_int("BIL_RETRIEVE_BATCH", d.retrieve_batch_size),
            max_concurrency=_int("BIL_MAX_CONCURRENCY", d.max_concurrency),
            max_retries=_int("BIL_MAX_RETRIES", d.max_retries),
            stats_limit=_int("BIL_STATS_LIMIT", d.stats_limit),
        )

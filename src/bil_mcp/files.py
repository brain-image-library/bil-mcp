"""List files of a dataset on the BIL download server.

The download server (https://download.brainimagelibrary.org) serves plain
HTML directory index pages. We parse the links in them, without
downloading any data.

UNTESTED AGAINST THE LIVE SERVER: the development sandboxes could not reach
download.brainimagelibrary.org. The parser handles the common Apache/nginx
autoindex layouts and is unit-tested on synthetic pages. Check it on your
own machine with ``pytest -m live``.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin

from .client import BILClient
from .models import FileEntry, FileListing, https_url_for

_SIZE_RE = re.compile(r"(\d{1,2}-\w{3}-\d{4} \d{2}:\d{2}|\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+([\d.]+[KMGTP]?|-)\s*$")


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []  # (href, trailing text)
        self._cur: str | None = None
        self._after: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._flush()
            self._cur = dict(attrs).get("href")

    def handle_data(self, data):
        if self._cur is not None:
            self._after.append(data)

    def _flush(self):
        if self._cur is not None:
            self.links.append((self._cur, "".join(self._after)))
        self._cur, self._after = None, []

    def close(self):
        super().close()
        self._flush()


def parse_index(html: str, base_url: str) -> list[FileEntry]:
    p = _LinkParser()
    p.feed(html)
    p.close()
    entries: list[FileEntry] = []
    for href, tail in p.links:
        if not href or href.startswith(("?", "#", "/", "mailto:", "..")) or "://" in href:
            continue  # sort links, parent dir, absolute links
        name = unquote(href)
        is_dir = name.endswith("/")
        modified = size = None
        # Text after the link is usually "name   2024-01-02 10:11   1.2G"
        m = _SIZE_RE.search(" ".join(tail.split()))
        if m:
            modified, size = m.group(1), (None if m.group(2) == "-" else m.group(2))
        entries.append(FileEntry(name=name.rstrip("/"), url=urljoin(base_url, href), is_dir=is_dir,
                                 size=size, modified=modified))
    return entries


async def list_files(client: BILClient, bildid: str, subpath: str = "", max_entries: int = 500) -> FileListing:
    rec = await client.retrieve(bildid)
    if rec is None:
        raise ValueError(f"No BIL dataset with id {bildid!r}.")
    ds = (rec.get("Dataset") or [{}])[0]
    base = https_url_for(ds.get("bildirectory"), client.s)
    if base is None:
        raise ValueError(f"Dataset {bildid} has no public bildirectory path.")
    sub = subpath.strip("/")
    if ".." in sub.split("/"):
        raise ValueError("subpath must not contain '..'")
    url = base + (sub + "/" if sub else "")
    try:
        status, html = await client.get_text(url)
    except Exception as e:  # network: most likely the download server is unreachable
        raise ValueError(
            f"Could not reach {url} ({type(e).__name__}, waited up to {client.s.download_timeout_s:.0f} s). "
            f"The BIL download server may be down for maintenance; check {client.s.status_url}. "
            f"Metadata tools still work. The dataset may also be accessible via Globus or on "
            f"Bridges-2 at {ds.get('bildirectory')}.") from e
    if status != 200:
        raise ValueError(f"Download server returned HTTP {status} for {url} (restricted or embargoed data?).")
    entries = parse_index(html, url)
    notes = [] if entries else ["No entries parsed: page format may have changed, or the directory is empty."]
    return FileListing(bildid=bildid, url=url, entries=entries[:max_entries],
                       truncated=len(entries) > max_entries, notes=notes)

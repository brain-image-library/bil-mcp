"""Directory-index parsing for list_dataset_files.

The page below is a synthetic Apache-style autoindex, NOT captured from
download.brainimagelibrary.org (unreachable from the build sandbox). The live
check is test_live.py::test_download_server_listing.
"""

import httpx
import pytest

from bil_mcp.client import BILClient
from bil_mcp.config import Settings
from bil_mcp.files import list_files, parse_index

APACHE = """<html><body><h1>Index of /cf/1c/cf1c1a431ef8d021</h1><pre>
<a href="?C=N;O=D">Name</a> <a href="?C=M;O=A">Last modified</a> <a href="?C=S;O=A">Size</a>
<hr><a href="/cf/1c/">Parent Directory</a>                             -
<a href="raw_data/">raw_data/</a>               2024-01-02 10:11    -
<a href="cell%20metadata.csv">cell metadata.csv</a>      2024-01-02 10:12  1.2G
</pre></body></html>"""


def test_parse_apache_index():
    base = "https://download.brainimagelibrary.org/cf/1c/cf1c1a431ef8d021/"
    e = parse_index(APACHE, base)
    assert [x.name for x in e] == ["raw_data", "cell metadata.csv"]
    assert e[0].is_dir and not e[1].is_dir
    assert e[1].size == "1.2G" and e[1].modified == "2024-01-02 10:12"
    assert e[1].url == base + "cell%20metadata.csv"


async def test_download_server_down_fails_fast_with_hint(fake):
    """API answers (fake), download server times out: error names the timeout and the status page."""
    seen_timeouts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "download.brainimagelibrary.org":
            seen_timeouts.append(request.extensions["timeout"]["read"])
            raise httpx.ConnectTimeout("timed out", request=request)
        return fake(request)

    c = BILClient(Settings(max_retries=0), transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError) as e:
        await list_files(c, "ace-ban-win")
    await c.aclose()
    assert seen_timeouts == [15.0]
    assert "waited up to 15 s" in str(e.value) and "datacentermove.html" in str(e.value)

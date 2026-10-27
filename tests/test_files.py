"""Directory-index parsing for list_dataset_files.

The page below is a synthetic Apache-style autoindex, NOT captured from
download.brainimagelibrary.org (unreachable from the build sandbox). The live
check is test_live.py::test_download_server_listing.
"""

from bil_mcp.files import parse_index

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

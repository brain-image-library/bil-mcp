"""End-to-end through the MCP protocol, in-process.

`Client(mcp)` connects to the server object directly (no subprocess), but
every call still goes through real MCP messages: initialize, tools/list,
tools/call, resources/read, prompts/get.
"""

import httpx
import pytest
from mcp.client import Client

import bil_mcp.server as srv
from bil_mcp.client import BILClient
from bil_mcp.config import Settings


@pytest.fixture
def connect(fake, monkeypatch):
    """Returns `connect()`, used as `async with connect() as c:` inside a test.

    (The MCP client runs background tasks; opening and closing it inside the
    same test task keeps anyio's cancel scopes happy, which a yield-fixture
    would not.)
    """
    monkeypatch.setattr(srv, "make_client", lambda: BILClient(Settings(max_retries=0),
                                                               transport=httpx.MockTransport(fake)))
    return lambda: Client(srv.mcp)


EXPECTED_TOOLS = {
    "search_datasets", "get_dataset", "get_dataset_summaries", "count_by_field", "find_related_datasets",
    "get_access_info", "list_dataset_files", "get_group_doi", "list_fields",
}


async def test_tools_are_listed_with_schemas(connect):
    async with connect() as mcp_client:
        tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
        assert set(tools) == EXPECTED_TOOLS
        s = tools["search_datasets"]
        assert "filters" in s.input_schema["properties"]
        assert s.output_schema is not None  # Pydantic return type -> structured output
        assert s.annotations.read_only_hint is True


async def test_search_tool_structured(connect):
    async with connect() as mcp_client:
        r = await mcp_client.call_tool("search_datasets", {"filters": {"species": "Human"}})
        assert not r.is_error
        assert r.structured_content["total"] == 1
        assert r.structured_content["datasets"][0]["bildid"] == "ace-can-ill"


async def test_bad_field_is_a_tool_error_not_a_crash(connect):
    async with connect() as mcp_client:
        r = await mcp_client.call_tool("search_datasets", {"filters": {"spieces": "Human"}})
        assert r.is_error and "Unknown filter field" in r.content[0].text


async def test_argument_validation(connect):
    async with connect() as mcp_client:
        r = await mcp_client.call_tool("search_datasets", {"filters": {"species": "Human"}, "limit": 1000})
        assert r.is_error


async def test_get_dataset_sections(connect):
    async with connect() as mcp_client:
        r = await mcp_client.call_tool("get_dataset", {"bildid": "rut", "sections": ["Specimen"]})
        assert list(r.structured_content) == ["Specimen"]


async def test_value_counts_and_access(connect):
    async with connect() as mcp_client:
        f = await mcp_client.call_tool("count_by_field", {"field": "consortium", "filters": {"technique": "MERFISH"}})
        assert f.structured_content["datasets_considered"] == 5
        a = await mcp_client.call_tool("get_access_info", {"bildid": "ace-ban-win"})
        assert a.structured_content["https_url"] == "https://download.brainimagelibrary.org/cf/1c/cf1c1a431ef8d021/"


async def test_group_doi_tool(connect):
    async with connect() as mcp_client:
        r = await mcp_client.call_tool("get_group_doi", {"group_id": "g.8"})
        assert "Zhuang, Xiaowei" in r.structured_content["creators"]


async def test_resources(connect):
    async with connect() as mcp_client:
        templates = (await mcp_client.list_resource_templates()).resource_templates
        assert [t.uri_template for t in templates] == ["bil://dataset/{bildid}"]
        fields = await mcp_client.read_resource("bil://fields")
        assert '"species"' in fields.contents[0].text
        ds = await mcp_client.read_resource("bil://dataset/rut")
        assert "96ba8210cceceeb7" in ds.contents[0].text


async def test_prompts(connect):
    async with connect() as mcp_client:
        names = {p.name for p in (await mcp_client.list_prompts()).prompts}
        assert names == {"find_datasets", "dataset_report"}
        p = await mcp_client.get_prompt("dataset_report", {"bildid": "rut"})
        assert "rut" in p.messages[0].content.text


async def test_unknown_argument_is_rejected_with_suggestion(connect):
    async with connect() as mcp_client:
        r = await mcp_client.call_tool("search_datasets", {"filters": {"species": "Human"}, "limt": 5})
        assert r.is_error
        assert "Unknown argument(s) for search_datasets: 'limt' (did you mean 'limit'?)" in r.content[0].text
        tools = {t.name: t for t in (await mcp_client.list_tools()).tools}
        assert all(t.input_schema.get("additionalProperties") is False for t in tools.values())


async def test_tool_calls_are_logged_with_arguments(connect, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="bil_mcp.audit")
    async with connect() as mcp_client:
        await mcp_client.call_tool("search_datasets", {"text": "xenium", "limit": 3})
        await mcp_client.call_tool("search_datasets", {"filters": {"species": "Human"}, "limt": 5})
    lines = [r.getMessage() for r in caplog.records if r.name == "bil_mcp.audit"]
    assert any('search_datasets {"text": "xenium", "limit": 3}' in l for l in lines)
    assert any(l.startswith("tool done") and " ok in " in l for l in lines)
    assert any(l.startswith("tool done") and " ERROR in " in l for l in lines)  # unknown argument

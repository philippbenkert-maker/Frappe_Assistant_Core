"""Unit tests for MCP token budgets that do not require a Frappe site."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from frappe_assistant_core.mcp.token_optimizer import (
    MCPOptimizationConfig,
    compact_json_dumps,
    compact_tool_result,
    concise_description,
    parse_tool_list,
    read_result_page,
    select_tool_metadata,
)


def _tools(*names):
    return [{"name": name, "description": name, "inputSchema": {}} for name in names]


def test_lean_profile_is_deterministic_and_bounded():
    tools = _tools(
        "delete_document",
        "get_result_page",
        "business_report",
        "get_document",
        "list_documents",
        "fetch",
        "search",
        "run_python_code",
    )
    config = MCPOptimizationConfig(profile="Lean", max_tools=5)

    selected = select_tool_metadata(tools, config)

    assert [tool["name"] for tool in selected] == [
        "search",
        "fetch",
        "list_documents",
        "get_document",
        "business_report",
    ]


def test_full_profile_is_explicit_escape_hatch():
    tools = _tools("one", "two", "three")
    config = MCPOptimizationConfig(profile="Full", max_tools=1)

    assert select_tool_metadata(tools, config) == tools


def test_custom_profile_adds_result_paging():
    tools = _tools("get_document", "get_result_page", "delete_document")
    config = MCPOptimizationConfig(profile="Custom", custom_tools=("get_document",), max_tools=10)

    assert [tool["name"] for tool in select_tool_metadata(tools, config)] == [
        "get_document",
        "get_result_page",
    ]


def test_parse_tool_list_accepts_json_commas_and_lines():
    assert parse_tool_list('["search", "fetch"]') == ("search", "fetch")
    assert parse_tool_list("search, fetch\nget_document") == (
        "search",
        "fetch",
        "get_document",
    )


def test_description_is_sentence_bounded():
    description = "First useful sentence. Second sentence is much longer and optional."
    assert concise_description(description, 25) == "First useful sentence."


def test_oversized_result_gets_continuation_and_hard_budget():
    value = {"success": True, "data": [{"name": f"ROW-{index}", "text": "x" * 200} for index in range(100)]}
    config = MCPOptimizationConfig(
        max_result_chars=2000,
        max_result_items=5,
        max_string_chars=100,
        result_ttl_seconds=900,
    )

    with patch(
        "frappe_assistant_core.mcp.token_optimizer.store_serialized_result",
        return_value="test-result-id",
    ):
        compacted, metrics = compact_tool_result(value, "user@example.com", config)

    assert metrics["truncated"] is True
    assert metrics["result_id"] == "test-result-id"
    assert compacted["_mcp_continuation"]["result_id"] == "test-result-id"
    assert compacted["_mcp_continuation"]["available_list_paths"][0] == {
        "path": "data",
        "items": 100,
    }
    assert len(compact_json_dumps(compacted)) <= config.max_result_chars


def test_small_result_is_unchanged():
    value = {"success": True, "data": [1, 2, 3]}
    compacted, metrics = compact_tool_result(
        value, "user@example.com", MCPOptimizationConfig(max_result_chars=2000)
    )

    assert compacted == value
    assert metrics["truncated"] is False


def test_result_pages_are_user_bound():
    payload = json.dumps(
        {
            "user": "owner@example.com",
            "serialized": json.dumps({"data": list(range(10))}),
        }
    )
    fake_frappe = SimpleNamespace(cache=SimpleNamespace(get_value=lambda _key: payload))

    with patch.dict(sys.modules, {"frappe": fake_frappe}):
        page = read_result_page("result-id", "owner@example.com", path="data", offset=3, limit=4)
        assert page["items"] == [3, 4, 5, 6]
        assert page["has_more"] is True
        assert page["next_offset"] == 7

        try:
            read_result_page("result-id", "other@example.com", path="data")
        except PermissionError:
            pass
        else:  # pragma: no cover - assertion branch
            raise AssertionError("cross-user result access must be denied")

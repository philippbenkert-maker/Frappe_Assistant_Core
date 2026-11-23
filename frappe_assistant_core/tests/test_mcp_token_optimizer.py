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
    serialize_tool_result,
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
        "get_result_page",
    ]


def test_bounded_profiles_keep_only_permitted_continuation_tools():
    tools = _tools("search", "fetch", "get_result_page", "delete_document")
    for profile in ("Lean", "Standard", "Custom"):
        config = MCPOptimizationConfig(profile=profile, custom_tools=("search", "fetch"), max_tools=2)
        assert [tool["name"] for tool in select_tool_metadata(tools, config)] == ["search", "get_result_page"]
        assert select_tool_metadata(tools[:2], config) == tools[:2]

        config.max_tools = 1
        assert select_tool_metadata(tools, config) == tools[2:3]


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


def test_pretty_tool_reply_is_budgeted_in_its_actual_format():
    from frappe_assistant_core.mcp.server import MCPServer

    config = MCPOptimizationConfig(max_result_chars=2000, max_result_items=2, compact_json=False)
    value = {"data": [{"name": f"SYNTHETIC-{n}", "description": "Wärme"} for n in range(30)]}
    assert len(compact_json_dumps(value)) <= config.max_result_chars
    assert len(serialize_tool_result(value, False)) > config.max_result_chars
    metrics = {}
    fake_frappe = SimpleNamespace(
        session=SimpleNamespace(user="synthetic@example.invalid"),
        local=SimpleNamespace(),
        logger=lambda: SimpleNamespace(debug=lambda *_: None, info=lambda *_: None),
    )

    with (
        patch.dict(sys.modules, {"frappe": fake_frappe}),
        patch("frappe_assistant_core.mcp.token_optimizer.get_optimization_config", return_value=config),
        patch("frappe_assistant_core.mcp.token_optimizer.store_serialized_result", return_value="owned-result") as store,
        patch("frappe_assistant_core.mcp.token_optimizer.update_audit_metrics",
              side_effect=lambda _tool, _args, recorded: metrics.update(recorded)),
    ):
        reply = MCPServer("local")._handle_tools_call(
            {"name": "synthetic_tool"}, {"synthetic_tool": {"fn": lambda: value}},
        )

    text = reply["content"][0]["text"]
    assert not reply["isError"]
    assert len(text) <= config.max_result_chars
    assert metrics["transmitted_chars"] == len(text)
    assert metrics["transmitted_bytes"] == len(text.encode("utf-8"))
    assert json.loads(text)["_mcp_continuation"]["result_id"] == "owned-result"
    assert json.loads(store.call_args.args[0]) == value


def test_direct_tool_errors_respect_the_wire_budget_without_a_continuation():
    from frappe_assistant_core.mcp.server import MCPServer

    budget = MCPOptimizationConfig(max_result_chars=2000)
    details = "Synthetic " + ("diagnostic " * 350)
    frappe = SimpleNamespace(
        session=SimpleNamespace(user="synthetic@example.invalid"),
        local=SimpleNamespace(),
        logger=lambda: SimpleNamespace(debug=lambda *_: None, info=lambda *_: None, error=lambda *_: None),
    )

    def fail():
        raise RuntimeError(details)

    with (
        patch.dict(sys.modules, {"frappe": frappe}),
        patch("frappe_assistant_core.mcp.token_optimizer.get_optimization_config", return_value=budget),
        patch("frappe_assistant_core.mcp.token_optimizer.store_serialized_result") as store,
    ):
        server = MCPServer("local")
        thrown = server._handle_tools_call({"name": "synthetic"}, {"synthetic": {"fn": fail}})
        unknown = server._handle_tools_call({"name": details}, {})
        ordinary = server._handle_tools_call({"name": "missing"}, {})
        budget.enabled = False
        unbounded = server._handle_tools_call({"name": "synthetic"}, {"synthetic": {"fn": fail}})

    for reply in (thrown, unknown):
        assert reply["isError"] is True
        text = reply["content"][0]["text"]
        assert len(text) <= budget.max_result_chars
        assert text.endswith("[error text truncated]")
    assert thrown["content"][0]["text"].startswith("Error executing synthetic: Synthetic")
    assert ordinary["content"][0]["text"] == "Tool 'missing' is not available for the current user or tool profile."
    assert unbounded["content"][0]["text"] == f"Error executing synthetic: {details}"
    store.assert_not_called()


def test_compaction_drops_path_hints_that_exceed_the_budget():
    value = {"long-key-" + "x" * 5000: [1, 2, 3]}
    config = MCPOptimizationConfig(max_result_chars=2000)
    with patch("frappe_assistant_core.mcp.token_optimizer.store_serialized_result", return_value="owned-result"):
        reply, _metrics = compact_tool_result(value, "synthetic@example.invalid", config)
    assert len(serialize_tool_result(reply, config.compact_json)) <= config.max_result_chars
    continuation = reply["_mcp_continuation"]
    assert "available_list_paths" not in continuation
    assert continuation["next"]["arguments"]["result_id"] == "owned-result"


def test_plain_text_budget_matches_wire_and_keeps_valid_cached_json():
    config = MCPOptimizationConfig(max_result_chars=2000)
    value = '"' * config.max_result_chars
    unchanged, metrics = compact_tool_result(value, "synthetic@example.invalid", config)
    assert unchanged == value
    assert not metrics["truncated"]
    longer = value + "ü"
    with patch("frappe_assistant_core.mcp.token_optimizer.store_serialized_result", return_value="owned-result") as store:
        reply, metrics = compact_tool_result(longer, "synthetic@example.invalid", config)
    assert metrics["truncated"]
    assert json.loads(store.call_args.args[0]) == longer
    assert len(serialize_tool_result(reply, config.compact_json)) <= config.max_result_chars


def test_continuation_reduces_list_page_without_losing_items():
    owner = "owner@example.invalid"
    value = [{"name": f"ROW-{index}", "description": "Wärme " * 120} for index in range(6)]
    config = MCPOptimizationConfig(max_result_chars=2000, compact_json=False)
    payload = {"user": owner, "serialized": json.dumps({"data": value})}
    fake_frappe = SimpleNamespace(cache=SimpleNamespace(get_value=lambda _key: payload))

    with patch.dict(sys.modules, {"frappe": fake_frappe}):
        rebuilt = []
        offset = 0
        while True:
            page = read_result_page("owned-result", owner, path="data", offset=offset, limit=6, config=config)
            assert page["result_id"] == "owned-result" and page["total_items"] == len(value)
            assert 0 < len(page["items"]) < len(value)
            assert len(serialize_tool_result(page, config.compact_json)) <= config.max_result_chars
            rebuilt.extend(page["items"])
            if not page["has_more"]:
                break
            assert page["next_offset"] == offset + len(page["items"]) > offset
            offset = page["next_offset"]
    assert rebuilt == value


def test_large_individual_row_is_recoverable_without_a_nested_continuation():
    owner = "owner@example.invalid"
    value = [{"name": "ROW-SMALL"}, {"name": "ROW-LARGE", "description": ('" Wärme \\ 雪\n' * 600)}]
    payload = {"user": owner, "serialized": json.dumps({"data": value})}
    fake_frappe = SimpleNamespace(cache=SimpleNamespace(get_value=lambda _key: payload))

    with patch.dict(sys.modules, {"frappe": fake_frappe}):
        for compact in (False, True):
            config = MCPOptimizationConfig(max_result_chars=2000, compact_json=compact)
            first = read_result_page("owned-result", owner, path="data", limit=20, config=config)
            assert first["items"] == value[:1] and first["next_offset"] == 1
            page = read_result_page("owned-result", owner, path="data", offset=1, limit=20, config=config)
            assert page["as_text"] and page["offset"] == 0
            chunks = []
            while True:
                result, metrics = compact_tool_result(page, owner, config)
                assert result == page and not metrics["truncated"]
                assert 0 < len(page["text"]) and page["result_id"] == "owned-result"
                chunks.append(page["text"])
                if not page["has_more"]:
                    break
                assert page["next_offset"] == page["offset"] + len(page["text"])
                page = read_result_page("owned-result", owner, path=page["path"], offset=page["next_offset"],
                                        limit=20, as_text=page["as_text"], config=config)
            assert json.loads("".join(chunks)) == value

        with patch("frappe_assistant_core.mcp.token_optimizer._resolve_path", side_effect=AssertionError):
            root_page = read_result_page("owned-result", owner, path="$", as_text=True, limit=1, config=config)
        assert root_page["path"] == "$" and root_page["text"].startswith('{"data":[')

"""Offline count/pagination regressions with the actual DocumentList executor."""
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def env(monkeypatch):
    frappe = ModuleType("frappe")
    frappe.__version__ = "16.50.0"
    frappe.session = SimpleNamespace(user="operator@example.invalid")
    frappe._ = lambda value: value
    frappe.get_list = Mock()
    frappe.log_error = Mock()
    frappe.get_meta = Mock(return_value=SimpleNamespace(is_submittable=False))
    base = ModuleType("frappe_assistant_core.core.base_tool")
    base.BaseTool = type("BaseTool", (), {})
    security = ModuleType("frappe_assistant_core.core.security_config")
    security.validate_document_access = Mock(return_value={"success": True, "role": "Default"})
    security.filter_sensitive_fields = lambda doc, *_args: doc
    security.SENSITIVE_FIELDS = {}
    security.ADMIN_ONLY_FIELDS = {}
    optimizer = ModuleType("frappe_assistant_core.mcp.token_optimizer")
    optimizer.get_optimization_config = lambda user: SimpleNamespace(max_list_rows=100)
    for module in (frappe, base, security, optimizer):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    path = Path(__file__).resolve().parents[1] / "frappe_assistant_core/plugins/core/tools/list_documents.py"
    spec = importlib.util.spec_from_file_location("count_regression_document_list", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "validate_query_fields", lambda *_args: {})
    monkeypatch.setattr(module, "resolve_unmatched_link_filters", lambda *_args: {})
    return SimpleNamespace(frappe=frappe, tool=module.DocumentList(), module=module, security=security)


@pytest.mark.parametrize("version", ["16.50.0", "16.0.0", "17.0.0-dev"])
def test_v16_count_uses_structured_aggregate_and_same_permissions(env, version):
    env.frappe.__version__ = version
    filters = {"attached_to_name": "PROJECT-1"}
    def query(doctype, **kwargs):
        assert doctype == "File" and kwargs["filters"] == filters
        assert kwargs["ignore_permissions"] is False
        if kwargs["fields"] == ["name"]:
            return [{"name": "file-1"}]
        if kwargs["fields"] != [{"COUNT": "name", "as": "count"}]:
            raise ValueError("SQL functions are not allowed as strings in SELECT")
        return [{"count": 3}]
    env.frappe.get_list.side_effect = query
    result = env.tool.execute({"doctype": "File", "filters": filters, "fields": ["name"], "limit": 1})
    assert result["success"] and result["total_count"] == 3 and result["has_more"] is True
    assert result["count_status"] == "verified"


def test_v15_retains_supported_legacy_count(env):
    env.frappe.__version__ = "15.92.0"
    env.frappe.get_list.side_effect = [[{"name": "file-1"}], [{"count": 1}]]
    result = env.tool.execute({"doctype": "File", "fields": ["name"]})
    assert env.frappe.get_list.call_args.kwargs["fields"] == ["count(name) as count"]
    assert result["total_count"] == 1 and result["has_more"] is False


@pytest.mark.parametrize("bad_count", [PermissionError("not permitted"), ValueError("query rejected"), [],
                                       [{"count": None}], [{"count": -1}], [{"count": False}],
                                       [{"count": "unavailable"}], [{"count": 0}]])
def test_failed_or_inconsistent_count_never_claims_zero_or_complete(env, bad_count):
    env.frappe.get_list.side_effect = [[{"name": "file-1"}], bad_count]
    result = env.tool.execute({"doctype": "File", "fields": ["name"], "limit": 1})
    assert result["success"] and result["count"] == 1
    assert result["total_count"] is None and result["has_more"] is None
    assert result["count_status"] == "unavailable"
    assert "complete list" in result["message"]
    assert env.frappe.get_list.call_count == 2  # No privileged fallback or silent retry.


def test_true_empty_count_remains_distinct_from_failure(env):
    env.frappe.get_list.side_effect = [[], [{"count": 0}]]
    result = env.tool.execute({"doctype": "File", "fields": ["name"]})
    assert result["success"] and result["count"] == result["total_count"] == 0
    assert result["has_more"] is False and result["count_status"] == "verified"


def test_user_scope_applies_to_rows_and_count(env):
    env.frappe.get_list.side_effect = [[{"name": env.frappe.session.user}], [{"count": 1}]]
    result = env.tool.execute({"doctype": "User", "fields": ["name"]})
    assert result["total_count"] == 1
    for call in env.frappe.get_list.call_args_list:
        assert call.kwargs["filters"] == {"name": env.frappe.session.user}
        assert call.kwargs["ignore_permissions"] is False


def test_submitted_filter_applies_to_rows_and_count(env):
    env.frappe.get_meta.return_value = SimpleNamespace(is_submittable=True)
    env.frappe.get_list.side_effect = [[{"name": "INV-1"}], [{"count": 1}]]
    result = env.tool.execute({"doctype": "Sales Invoice", "fields": ["name"]})
    assert result["total_count"] == 1
    for call in env.frappe.get_list.call_args_list:
        assert call.kwargs["filters"] == {"docstatus": 1}
        assert call.kwargs["ignore_permissions"] is False


@pytest.mark.parametrize("condition", [
    ["title", "=", "docstatus"],
    ["Sales Invoice", "title", "=", "docstatus"],
])
def test_docstatus_as_filter_value_does_not_remove_submitted_default(env, condition):
    env.frappe.get_meta.return_value = SimpleNamespace(is_submittable=True)
    env.frappe.get_list.side_effect = [[{"name": "INV-1"}], [{"count": 1}]]

    result = env.tool.execute({"doctype": "Sales Invoice", "filters": condition, "fields": ["name"]})

    expected = [condition, ["docstatus", "=", 1]]
    assert result["success"] and result["filters_applied"] == expected
    assert result["total_count"] == 1 and result["has_more"] is False
    assert [call.kwargs["filters"] for call in env.frappe.get_list.call_args_list] == [expected, expected]


@pytest.mark.parametrize("condition", [["docstatus", "=", 2], ["Sales Invoice", "docstatus", "=", 2]])
def test_single_explicit_docstatus_filter_stays_explicit(env, condition):
    env.frappe.get_meta.return_value = SimpleNamespace(is_submittable=True)
    env.frappe.get_list.side_effect = [[{"name": "INV-1"}], [{"count": 1}]]

    result = env.tool.execute({"doctype": "Sales Invoice", "filters": condition, "fields": ["name"]})

    assert result["success"] and result["filters_applied"] == [condition]
    assert [call.kwargs["filters"] for call in env.frappe.get_list.call_args_list] == [[condition], [condition]]


@pytest.mark.parametrize("role", ["Default", "Assistant User"])
def test_user_scope_keeps_existing_list_conditions(env, role):
    env.security.validate_document_access.return_value = {"success": True, "role": role}
    env.frappe.get_list.side_effect = [[], [{"count": 0}]]
    requested = [["enabled", "=", 1]]

    result = env.tool.execute({"doctype": "User", "filters": requested, "fields": ["name"]})

    expected = [["enabled", "=", 1], ["name", "=", env.frappe.session.user]]
    assert requested == [["enabled", "=", 1]]
    assert result["success"] and result["filters_applied"] == expected and result["total_count"] == 0
    for call in env.frappe.get_list.call_args_list:
        assert call.kwargs["filters"] == expected and call.kwargs["ignore_permissions"] is False


@pytest.mark.parametrize("requested_name, condition", [
    ("other@example.invalid", ["name", "=", "other@example.invalid"]),
    (["!=", "operator@example.invalid"], ["name", "!=", "operator@example.invalid"]),
    (["in", ["operator@example.invalid", "other@example.invalid"]],
     ["name", "in", ["operator@example.invalid", "other@example.invalid"]]),
])
def test_user_scope_intersects_mapping_name_conditions(env, requested_name, condition):
    env.frappe.get_list.side_effect = [[], [{"count": 0}]]
    requested = {"name": requested_name, "enabled": 1}
    result = env.tool.execute({"doctype": "User", "filters": requested, "fields": ["name"]})

    expected = [condition, ["enabled", "=", 1], ["name", "=", env.frappe.session.user]]
    assert requested == {"name": requested_name, "enabled": 1}
    assert result["success"] and result["total_count"] == 0
    for call in env.frappe.get_list.call_args_list:
        assert call.kwargs["filters"] == expected and call.kwargs["ignore_permissions"] is False

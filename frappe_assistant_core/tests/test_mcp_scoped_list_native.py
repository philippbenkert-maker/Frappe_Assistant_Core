"""Exercise the MCP list and continuation against disposable Frappe data."""

import json
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import frappe

from frappe_assistant_core.core.security_config import get_user_primary_role
from frappe_assistant_core.mcp.server import MCPServer
from frappe_assistant_core.mcp.token_optimizer import MCPOptimizationConfig, _cache_key
from frappe_assistant_core.mcp.tool_adapter import register_base_tool
from frappe_assistant_core.plugins.core.tools.get_result_page import GetResultPage
from frappe_assistant_core.plugins.core.tools.list_documents import DocumentList


class TestMCPScopedListNative(unittest.TestCase):
    def setUp(self):
        if frappe.local.site != "test_site" or not frappe.flags.in_test:
            message = "Requires the disposable Frappe CI test site"
            if os.environ.get("FAC_REQUIRE_NATIVE_SCOPE_TEST") == "1":
                self.fail(message)
            self.skipTest(message)
        self.original_user = frappe.session.user
        self.original_in_test = frappe.in_test
        self.marker = uuid4().hex[:12]
        self.savepoint = f"fac_mcp_scope_{self.marker}"
        self.results = []
        frappe.set_user("Administrator")
        frappe.in_test = True
        frappe.db.savepoint(self.savepoint)
        self.addCleanup(self.cleanup_fixture)
        self.server = MCPServer("fac-disposable")
        for tool in (DocumentList(), GetResultPage()):
            register_base_tool(self.server, tool)
        self.config = MCPOptimizationConfig(
            compact_json=False, max_result_chars=2000, max_result_items=2, max_list_rows=20,
        )

    def cleanup_fixture(self):
        frappe.set_user("Administrator")
        try:
            for result_id in self.results:
                frappe.cache.delete_value(_cache_key(result_id))
        finally:
            try:
                frappe.db.rollback(save_point=self.savepoint)
            finally:
                frappe.in_test = self.original_in_test
                frappe.set_user(self.original_user)

    def create_user(self, label):
        return frappe.get_doc({
            "doctype": "User", "email": f"fac-{label}-{self.marker}@example.invalid",
            "first_name": f"FAC synthetic {label}", "user_type": "System User",
            "enabled": 1, "send_welcome_email": 0, "roles": [{"role": "Assistant User"}],
        }).insert(ignore_permissions=True).name

    def create_todo(self, user, label, status="Open"):
        return frappe.get_doc({
            "doctype": "ToDo", "allocated_to": user, "assigned_by": "Administrator",
            "status": status, "description": f"{label}-{self.marker} " + "Wärmepumpe " * 85,
        }).insert(ignore_permissions=True).name

    def call(self, name, arguments, *, is_error=False):
        with patch(
            "frappe_assistant_core.mcp.token_optimizer.get_optimization_config", return_value=self.config,
        ):
            reply = self.server._handle_tools_call({"name": name, "arguments": arguments})
        text = reply["content"][0]["text"]
        self.assertLessEqual(len(text), self.config.max_result_chars)
        payload = json.loads(text)
        continuation = payload.get("_mcp_continuation", {})
        if continuation.get("result_id"):
            self.results.append(continuation["result_id"])
        self.assertEqual(reply["isError"], is_error)
        return payload

    def test_assistant_role_filters_and_continuation_respect_native_permissions(self):
        reader = self.create_user("reader")
        other = self.create_user("other")
        allowed = sorted(self.create_todo(reader, f"visible-{index}") for index in range(4))
        closed = self.create_todo(reader, "closed", status="Closed")
        foreign = self.create_todo(other, "foreign")
        arguments = {
            "doctype": "ToDo", "fields": ["name", "description"],
            "filters": {"name": ["in", [*allowed, closed, foreign]], "status": "Open"},
            "order_by": "name asc", "limit": 10,
        }

        frappe.set_user(reader)
        self.assertEqual(get_user_primary_role(reader), "Assistant User")
        rejected = self.call("list_documents", {**arguments, "filters": {"wrong_status_field": "Open"}}, is_error=True)
        self.assertEqual(rejected["error_type"], "invalid_query_fields")
        result = self.call("list_documents", arguments)
        self.assertEqual((result["count"], result["total_count"]), (4, 4))
        self.assertEqual((result["count_status"], result["has_more"]), ("verified", False))
        self.assertEqual(result["data"][0]["name"], allowed[0])
        result_id = result["_mcp_continuation"]["result_id"]

        limited = self.call("list_documents", {**arguments, "fields": ["name"], "limit": 1})
        self.assertEqual(limited["data"], [{"name": allowed[0]}])
        self.assertEqual((limited["count"], limited["total_count"], limited["has_more"]), (1, 4, True))

        for offset, name in enumerate(allowed):
            page = self.call("get_result_page", {"result_id": result_id, "path": "data", "offset": offset, "limit": 1})
            self.assertEqual([record["name"] for record in page["items"]], [name])
            self.assertEqual(page["total_items"], 4)

        frappe.set_user(other)
        self.assertEqual(get_user_primary_role(other), "Assistant User")
        rejection = self.call("get_result_page", {"result_id": result_id, "path": "data"}, is_error=True)
        self.assertNotIn("items", rejection)
        own_result = self.call("list_documents", {**arguments, "fields": ["name"]})
        self.assertEqual(own_result["data"], [{"name": foreign}])
        self.assertEqual(own_result["total_count"], 1)
        self.assertEqual(own_result["count_status"], "verified")
        inaccessible = self.call(
            "list_documents", {**arguments, "fields": ["name"], "filters": {"name": ["in", allowed]}},
        )
        self.assertEqual(inaccessible["data"], [])
        self.assertEqual((inaccessible["total_count"], inaccessible["count_status"]), (0, "verified"))

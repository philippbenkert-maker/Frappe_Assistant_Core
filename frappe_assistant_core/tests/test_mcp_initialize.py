"""MCP discovery contract regressions."""

from unittest.mock import MagicMock, patch

from frappe_assistant_core import __version__
from frappe_assistant_core.mcp.server import MCPServer
from frappe_assistant_core.tests.base_test import BaseAssistantTest


class TestMCPInitialize(BaseAssistantTest):
    def test_initialize_advertises_current_version_and_catalog_changes(self):
        settings = MagicMock(mcp_protocol_version="2025-06-18")
        with patch("frappe.get_single", return_value=settings):
            result = MCPServer("frappe-assistant-core")._handle_initialize({})

        self.assertEqual(result["serverInfo"]["version"], __version__)
        self.assertTrue(result["capabilities"]["tools"]["listChanged"])
        self.assertTrue(result["capabilities"]["resources"]["listChanged"])

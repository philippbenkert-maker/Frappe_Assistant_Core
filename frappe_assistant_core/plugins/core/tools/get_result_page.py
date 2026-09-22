# Frappe Assistant Core - AI Assistant integration for Frappe Framework
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Continuation tool for MCP results that exceeded the configured budget."""

from typing import Any, Dict

import frappe

from frappe_assistant_core.core.base_tool import BaseTool
from frappe_assistant_core.mcp.token_optimizer import read_result_page


class GetResultPage(BaseTool):
    def __init__(self):
        super().__init__()
        self.name = "get_result_page"
        self.description = (
            "Read the next page of an earlier MCP result using its result_id. "
            "Use the path and next_offset returned by the previous response."
        )
        self.requires_permission = None
        self.inputSchema = {
            "type": "object",
            "properties": {
                "result_id": {
                    "type": "string",
                    "description": "Opaque result_id from _mcp_continuation.",
                },
                "path": {
                    "type": "string",
                    "description": "Optional available_list_paths entry. The largest list is used by default.",
                },
                "offset": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Zero-based item or character offset.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                    "description": "Maximum list items to return.",
                },
            },
            "required": ["result_id"],
        }

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return read_result_page(
            result_id=arguments.get("result_id", ""),
            user=frappe.session.user,
            path=arguments.get("path"),
            offset=arguments.get("offset", 0),
            limit=arguments.get("limit", 20),
        )


get_result_page = GetResultPage

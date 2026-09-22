# Frappe Assistant Core - AI Assistant integration for Frappe Framework
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Compact report discovery, requirements, and execution through one tool."""

from numbers import Number
from typing import Any, Dict, List

import frappe

from frappe_assistant_core.core.base_tool import BaseTool
from frappe_assistant_core.mcp.token_optimizer import (
    compact_json_dumps,
    get_optimization_config,
    store_serialized_result,
)


def _numeric_summary(rows: List[Any]) -> Dict[str, Dict[str, float]]:
    values: Dict[str, List[float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            if isinstance(value, Number) and not isinstance(value, bool):
                values.setdefault(str(key), []).append(float(value))

    summary = {}
    for key, numbers in list(values.items())[:20]:
        if not numbers:
            continue
        summary[key] = {
            "count": len(numbers),
            "sum": sum(numbers),
            "min": min(numbers),
            "max": max(numbers),
            "average": sum(numbers) / len(numbers),
        }
    return summary


class BusinessReport(BaseTool):
    """Replace the three-report-tool catalog with a single compact contract."""

    def __init__(self):
        super().__init__()
        self.name = "business_report"
        self.description = (
            "Discover reports, inspect one report's required filters, or run it. "
            "Run responses contain a numeric summary and a bounded row preview; use "
            "get_result_page with result_id when more rows are needed."
        )
        self.requires_permission = None
        self.inputSchema = {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["discover", "requirements", "run"],
                    "description": "discover, requirements, or run",
                },
                "report_name": {
                    "type": "string",
                    "description": "Exact report name for requirements/run.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional case-insensitive report-name/module filter for discover.",
                },
                "module": {"type": "string", "description": "Optional module filter for discover."},
                "report_type": {
                    "type": "string",
                    "enum": ["Query Report", "Script Report"],
                    "description": "Optional report type filter for discover.",
                },
                "filters": {
                    "type": "object",
                    "default": {},
                    "description": "Report filters for run. Use requirements when exact values are unknown.",
                },
                "max_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Preview rows; the site default is used when omitted.",
                },
            },
            "required": ["action"],
        }

    def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from .report_requirements import ReportRequirements
        from .report_tools import ReportTools

        action = arguments.get("action")
        config = get_optimization_config(frappe.session.user)
        requested_rows = arguments.get("max_rows", config.max_report_rows)
        max_rows = max(
            1,
            min(
                int(requested_rows or config.max_report_rows),
                config.max_report_rows,
                100,
            ),
        )

        if action == "discover":
            result = ReportTools.list_reports(
                module=arguments.get("module"), report_type=arguments.get("report_type")
            )
            reports = result.get("reports", []) if isinstance(result, dict) else []
            query = (arguments.get("query") or "").strip().lower()
            if query:
                reports = [
                    row
                    for row in reports
                    if query
                    in " ".join(
                        str(row.get(key, "")) for key in ("name", "report_name", "module", "report_type")
                    ).lower()
                ]
            return {
                "success": result.get("success", True),
                "reports": reports[:max_rows],
                "count": len(reports),
                "has_more": len(reports) > max_rows,
            }

        report_name = arguments.get("report_name")
        if not report_name:
            return {"success": False, "error": "report_name is required for this action"}

        if action == "requirements":
            return ReportRequirements().execute(
                {
                    "report_name": report_name,
                    "include_metadata": False,
                    "include_columns": True,
                    "include_filters": True,
                }
            )

        if action != "run":
            return {"success": False, "error": f"Unsupported action: {action}"}

        result = ReportTools.execute_report(report_name, arguments.get("filters", {}), "json")
        if not result.get("success"):
            # Filter errors become self-healing: return the contract in the same call.
            requirements = ReportRequirements().execute(
                {
                    "report_name": report_name,
                    "include_metadata": False,
                    "include_columns": False,
                    "include_filters": True,
                }
            )
            result["requirements"] = requirements
            return result

        rows = result.get("data", []) or []
        response = dict(result)
        response["data"] = rows[:max_rows]
        response["data_count"] = len(rows)
        response["preview_count"] = len(response["data"])
        response["has_more"] = len(rows) > max_rows
        response["numeric_summary"] = _numeric_summary(rows)

        if len(rows) > max_rows:
            result_id = store_serialized_result(
                compact_json_dumps(result), frappe.session.user, config.result_ttl_seconds
            )
            response["result_id"] = result_id
            response["continuation"] = {
                "tool": "get_result_page",
                "arguments": {"result_id": result_id, "path": "data", "offset": max_rows},
            }
        return response


business_report = BusinessReport

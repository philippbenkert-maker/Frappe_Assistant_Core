"""Regression tests for exact query-field validation in list_documents."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from frappe_assistant_core.plugins.core.tools.list_documents import (
    DocumentList,
    field_suggestions,
    validate_query_fields,
)


class QueryMeta:
    def __init__(self, *fields):
        self.fields = [SimpleNamespace(**field) for field in fields]
        self.is_submittable = 0
        self._fields = {field.fieldname: field for field in self.fields}

    def get_field(self, fieldname):
        return self._fields.get(fieldname)


QUOTATION_META = QueryMeta(
    {"fieldname": "party_name", "label": "Party", "fieldtype": "Dynamic Link"},
    {"fieldname": "customer_name", "label": "Customer Name", "fieldtype": "Data"},
    {"fieldname": "title", "label": "Title", "fieldtype": "Data"},
    {"fieldname": "grand_total", "label": "Grand Total", "fieldtype": "Currency"},
)

PROJECT_META = QueryMeta(
    {"fieldname": "title", "label": "Titel / Kommission", "fieldtype": "Data"},
    {"fieldname": "project_origin", "label": "Projektursprung", "fieldtype": "Select"},
    {"fieldname": "customer", "label": "Hauptgeschäftspartner", "fieldtype": "Link"},
)


class TestQueryFieldValidation(unittest.TestCase):
    def test_unknown_filter_reports_exact_candidates(self):
        with patch(
            "frappe_assistant_core.plugins.core.tools.list_documents.frappe.get_meta",
            return_value=QUOTATION_META,
        ):
            result = validate_query_fields(
                "Quotation",
                {"customer": "Guyer Wärme und Wasser AG"},
                ["name", "title", "grand_total"],
                "modified desc",
            )

        self.assertEqual(result["error_type"], "invalid_query_fields")
        self.assertEqual(result["invalid_filters"], ["customer"])
        suggestions = [row["fieldname"] for row in result["field_suggestions"]["customer"]]
        self.assertIn("party_name", suggestions)
        self.assertIn("customer_name", suggestions)
        self.assertTrue(result["retryable"])

    def test_project_name_points_to_title_candidate(self):
        suggestions = field_suggestions(PROJECT_META, "project_name")
        self.assertEqual(suggestions[0]["fieldname"], "title")

    def test_unknown_selected_and_order_fields_are_reported(self):
        with patch(
            "frappe_assistant_core.plugins.core.tools.list_documents.frappe.get_meta",
            return_value=PROJECT_META,
        ):
            result = validate_query_fields(
                "KT Objektakte",
                {},
                ["name", "project_name"],
                "project_name asc",
            )

        self.assertEqual(result["invalid_fields"], ["project_name"])
        self.assertEqual(result["invalid_order_by"], ["project_name"])

    def test_valid_and_advanced_query_fields_pass(self):
        with patch(
            "frappe_assistant_core.plugins.core.tools.list_documents.frappe.get_meta",
            return_value=QUOTATION_META,
        ):
            result = validate_query_fields(
                "Quotation",
                {"party_name": "Guyer Wärme und Wasser AG", "docstatus": [0, 1]},
                ["name", "title", "count(name) as count"],
                "modified desc",
            )

        self.assertEqual(result, {})

    def test_execute_stops_before_database_query(self):
        with patch(
            "frappe_assistant_core.core.security_config.validate_document_access",
            return_value={"success": True, "role": "Default"},
        ), patch(
            "frappe_assistant_core.mcp.token_optimizer.get_optimization_config",
            return_value=MagicMock(max_list_rows=1000),
        ), patch(
            "frappe_assistant_core.plugins.core.tools.list_documents.frappe.session",
            MagicMock(user="analyst@example.com"),
        ), patch(
            "frappe_assistant_core.plugins.core.tools.list_documents.frappe.get_meta",
            return_value=QUOTATION_META,
        ), patch(
            "frappe_assistant_core.plugins.core.tools.list_documents.frappe.get_list"
        ) as get_list, patch(
            "frappe_assistant_core.plugins.core.tools.list_documents.frappe.log_error"
        ) as log_error:
            result = DocumentList().execute(
                {"doctype": "Quotation", "filters": {"customer": "Guyer Wärme und Wasser AG"}}
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "invalid_query_fields")
        get_list.assert_not_called()
        log_error.assert_not_called()

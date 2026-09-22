"""Install update-safe MCP token controls on existing sites."""

import frappe

USER_FIELDS = (
    {
        "fieldname": "assistant_mcp_tool_profile",
        "label": "Assistant MCP Tool Profile",
        "fieldtype": "Select",
        "insert_after": "assistant_enabled",
        "options": "Inherit\nLean\nStandard\nFull\nCustom",
        "default": "Inherit",
        "description": "Per-user MCP tool profile. Lean minimizes model context and credit usage.",
        "print_hide": 1,
    },
    {
        "fieldname": "assistant_mcp_allowed_tools",
        "label": "Assistant MCP Allowed Tools",
        "fieldtype": "Small Text",
        "insert_after": "assistant_mcp_tool_profile",
        "depends_on": "eval:doc.assistant_mcp_tool_profile=='Custom'",
        "description": "Comma- or newline-separated tool names for the Custom profile.",
        "print_hide": 1,
    },
)


def _ensure_user_fields():
    for definition in USER_FIELDS:
        fieldname = definition["fieldname"]
        existing = frappe.db.exists("Custom Field", {"dt": "User", "fieldname": fieldname})
        if existing:
            doc = frappe.get_doc("Custom Field", existing)
            changed = False
            for key, value in definition.items():
                if doc.get(key) != value:
                    doc.set(key, value)
                    changed = True
            if changed:
                doc.save(ignore_permissions=True)
            continue

        doc = frappe.new_doc("Custom Field")
        doc.dt = "User"
        for key, value in definition.items():
            doc.set(key, value)
        doc.insert(ignore_permissions=True)


def execute():
    frappe.reload_doc("assistant_core", "doctype", "assistant_core_settings")
    frappe.reload_doc("assistant_core", "doctype", "assistant_audit_log")
    _ensure_user_fields()

    defaults = {
        "mcp_token_optimization_enabled": 1,
        "mcp_default_tool_profile": "Lean",
        "mcp_max_tools": 10,
        "mcp_max_result_chars": 12000,
        "mcp_max_result_items": 20,
        "mcp_max_string_chars": 4000,
        "mcp_result_ttl_seconds": 900,
        "mcp_compact_json": 1,
        "mcp_concise_descriptions": 1,
        "mcp_max_description_chars": 280,
        "mcp_max_list_rows": 50,
        "mcp_max_report_rows": 20,
        "mcp_max_ocr_pages": 5,
        "mcp_max_code_output_chars": 16000,
        "skill_mode": "replace",
    }
    for fieldname, value in defaults.items():
        current = frappe.db.get_single_value("Assistant Core Settings", fieldname)
        # skill_mode is intentionally migrated to the token-efficient mode.
        if current in (None, "") or fieldname == "skill_mode":
            frappe.db.set_single_value("Assistant Core Settings", fieldname, value)

    frappe.clear_cache(doctype="User")

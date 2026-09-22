"""Persist the token-saving checkbox defaults on upgraded sites.

Frappe exposes an unset Check field as ``0`` even when the DocField default is
``1``.  The original v2.6 patch therefore treated the freshly added fields as
existing administrator choices and left them disabled.  This one-time patch
stores the intended production defaults explicitly; administrators remain free
to change them after the migration.
"""

import frappe


def execute():
    defaults = {
        "mcp_token_optimization_enabled": 1,
        "mcp_compact_json": 1,
        "mcp_concise_descriptions": 1,
        "skill_mode": "replace",
    }
    for fieldname, value in defaults.items():
        frappe.db.set_single_value("Assistant Core Settings", fieldname, value)

    frappe.clear_cache(doctype="Assistant Core Settings")

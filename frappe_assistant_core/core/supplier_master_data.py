"""Read-only supplier completeness checks for MCP document workflows.

An existing Supplier is not proof that its address or payment details were
captured. In particular, invoice remarks are not supplier master data.
"""

import frappe
from frappe.utils import validate_iban

from frappe_assistant_core.core.security_config import (
    filter_sensitive_fields,
    validate_document_access,
)


def _access(doctype, name=None):
    access = validate_document_access(
        user=frappe.session.user, doctype=doctype, name=name, perm_type="read"
    )
    if not access.get("success"):
        raise PermissionError("Supplier master data cannot be verified with current permissions")
    return access["role"]


def _read(doctype, name):
    role = _access(doctype, name)
    row = filter_sensitive_fields(frappe.get_doc(doctype, name).as_dict(), doctype, role)
    checked_fields = ("iban", "supplier_primary_address", "default_bank_account", "links",
                      "address_line1", "city", "country", "pincode", "party", "party_type")
    if row.get("error") or any(row.get(field) == "***RESTRICTED***" for field in checked_fields):
        raise PermissionError("Relevant supplier fields are restricted")
    return row


def _list(doctype, filters):
    _access(doctype)
    rows = frappe.get_list(doctype, filters=filters, fields=["name"], limit_page_length=101)
    if len(rows) > 100:
        raise ValueError("Supplier master data review needs a narrower scope")
    return rows


def _text(value):
    return str(value or "").strip()


def _linked_address(address, supplier):
    return any(
        row.get("link_doctype") == "Supplier" and row.get("link_name") == supplier
        for row in address.get("links") or []
    )


def _complete_address(address, supplier):
    if not _linked_address(address, supplier) or address.get("disabled"):
        return False
    fields = ["address_line1", "city", "country"]
    if address.get("country") in ("Switzerland", "Schweiz", "Suisse", "Svizzera", "Liechtenstein"):
        fields.append("pincode")
    return all(_text(address.get(field)) for field in fields)


def review_supplier_master_data(doc):
    """Return structural findings, never payment authorization or database writes.

    Missing read access is *unverifiable*, not evidence of missing records. A
    blank primary-address shortcut is not evidence that no linked Address exists.
    """
    if doc.doctype not in ("Supplier", "Purchase Invoice"):
        return None

    review = {
        "status": "needs_review",
        "complete": False,
        "payment_authorized": False,
        "issues": [],
        "next_steps": [],
    }
    issues = review["issues"]
    try:
        supplier_name = doc.name if doc.doctype == "Supplier" else doc.get("supplier")
        if not supplier_name:
            issues.append("supplier_missing")
            review["next_steps"] = ["Identify the invoice supplier from source evidence before proceeding."]
            return review
        supplier = _read("Supplier", supplier_name)

        address_names = {
            row["name"]
            for row in _list(
                "Address",
                [["Dynamic Link", "link_doctype", "=", "Supplier"],
                 ["Dynamic Link", "link_name", "=", supplier_name]],
            )
        }
        primary = supplier.get("supplier_primary_address")
        selected = doc.get("supplier_address") if doc.doctype == "Purchase Invoice" else None
        address_names.update(name for name in (primary, selected) if name)
        complete_addresses = set()
        for name in sorted(address_names):
            address = _read("Address", name)
            if _complete_address(address, supplier_name):
                complete_addresses.add(name)
        if not complete_addresses:
            issues.append("supplier_address_missing_or_incomplete")
        if primary and primary not in complete_addresses:
            issues.append("supplier_primary_address_invalid")
        if doc.doctype == "Purchase Invoice" and selected not in complete_addresses:
            issues.append("invoice_supplier_address_missing_or_invalid")

        bank_names = {
            row["name"] for row in _list("Bank Account", {"party_type": "Supplier", "party": supplier_name})
        }
        default_bank = supplier.get("default_bank_account")
        if default_bank:
            bank_names.add(default_bank)
        ibans = []
        if _text(supplier.get("iban")):
            ibans.append(supplier["iban"])
        for name in sorted(bank_names):
            bank = _read("Bank Account", name)
            if bank.get("disabled") or bank.get("is_company_account") or (
                bank.get("party_type") != "Supplier" or bank.get("party") != supplier_name
            ):
                if name == default_bank:
                    issues.append("supplier_default_bank_invalid")
                continue
            if _text(bank.get("iban")):
                ibans.append(bank["iban"])
        if not ibans:
            issues.append("supplier_payment_details_need_review")
        elif not all(validate_iban(_text(iban)) for iban in ibans):
            issues.append("supplier_iban_invalid")
        review["complete"] = not issues
        review["status"] = "structurally_complete" if not issues else "needs_review"
    except Exception:
        # Do not leak bank values or treat a query/permission failure as absence.
        review["status"] = "unverifiable"
        issues.append("supplier_master_data_unverifiable")

    review["next_steps"] = [
        "Read the source invoice including its payment section; do not infer missing values.",
        "Verify a complete Address linked to Supplier; select it on the invoice.",
        "Verify payment details in Supplier.iban or a supplier-owned Bank Account, not only in remarks.",
        "Review QR references, changed bank details, cash/card and non-IBAN exceptions manually; never invent an IBAN.",
        "Read back Supplier, Address, Bank Account and invoice links before declaring the import complete.",
    ] if issues else [
        "Structural completeness is not source matching or payment approval; compare with the original invoice."
    ]
    return review


def submission_blocker(review):
    if review and not review["complete"]:
        return {
            "success": False,
            "error_type": "supplier_master_data_incomplete",
            "error": "Purchase Invoice submission blocked: supplier master data needs review.",
            "submitted": False,
            "master_data_review": review,
            "suggestion": "Complete and verify the linked master data or review payment exceptions in ERP. Do not bypass this check through another tool.",
        }
    return None


def add_master_data_review(result, review):
    if review:
        result["master_data_review"] = review
        result["import_complete"] = False
        result.setdefault("next_steps", []).extend(review["next_steps"])
        if not review["complete"]:
            result["message"] += " Supplier master data is incomplete or unverified; the invoice import is not complete."
    return result

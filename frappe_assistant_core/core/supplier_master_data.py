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
    checked_fields = (
        "iban",
        "supplier_primary_address",
        "default_bank_account",
        "default_payment_method",
        "esr_participation_number",
        "links",
        "address_line1",
        "city",
        "country",
        "pincode",
        "party",
        "party_type",
        "disabled",
        "is_company_account",
    )
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


def _normalize_identifier(value):
    return "".join(_text(value).split()).upper()


def _is_qr_iban(value):
    normalized = _normalize_identifier(value)
    if len(normalized) != 21 or not normalized.startswith("CH"):
        return False
    iid = normalized[4:9]
    return iid.isdigit() and 30000 <= int(iid) <= 31999


def _valid_qr_reference(value):
    normalized = _normalize_identifier(value)
    if len(normalized) != 27 or not normalized.isdigit():
        return False

    carry = 0
    lookup = (0, 9, 4, 6, 8, 2, 7, 1, 3, 5)
    for digit in normalized[:-1]:
        carry = lookup[(carry + int(digit)) % 10]
    return (10 - carry) % 10 == int(normalized[-1])


def _valid_iban(value):
    try:
        return bool(validate_iban(_text(value)))
    except Exception:
        return False


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


def _supplier_addresses(supplier_name, supplier, selected=None):
    address_names = {
        row["name"]
        for row in _list(
            "Address",
            [["Dynamic Link", "link_doctype", "=", "Supplier"],
             ["Dynamic Link", "link_name", "=", supplier_name]],
        )
    }
    primary = supplier.get("supplier_primary_address")
    address_names.update(name for name in (primary, selected) if name)
    addresses = {
        name: _read("Address", name)
        for name in sorted(address_names)
    }
    complete = {
        name for name, address in addresses.items()
        if _complete_address(address, supplier_name)
    }
    return primary, complete


def prepare_purchase_invoice_defaults(doc):
    """Fill only unambiguous, verified supplier defaults on a new invoice doc.

    This changes the in-memory document only. It never writes Supplier, Address,
    or Bank Account records; the normal review must still approve the result.
    """
    if doc.doctype != "Purchase Invoice" or not _text(doc.get("supplier")):
        return {}

    applied = {}
    try:
        supplier_name = _text(doc.get("supplier"))
        supplier = _read("Supplier", supplier_name)

        if not _text(doc.get("payment_type")):
            method = _text(supplier.get("default_payment_method")) or "IBAN"
            if method in ("IBAN", "ESR", "SEPA"):
                doc.payment_type = method
                applied["payment_type"] = method

        if not _text(doc.get("supplier_address")):
            primary, complete = _supplier_addresses(supplier_name, supplier)
            if primary in complete:
                doc.supplier_address = primary
                applied["supplier_address"] = primary
            elif len(complete) == 1:
                address = next(iter(complete))
                doc.supplier_address = address
                applied["supplier_address"] = address
    except Exception:
        # The read-only review below reports permission/query failures as
        # unverifiable and blocks persistence. Never guess defaults on failure.
        return applied
    return applied


def _supplier_bank_iban(supplier_name, supplier, issues):
    """Return one verified supplier-owned bank IBAN, without choosing at random."""
    names = {
        row["name"]
        for row in _list("Bank Account", {"party_type": "Supplier", "party": supplier_name})
    }
    default_bank = supplier.get("default_bank_account")
    if default_bank:
        names.add(default_bank)

    eligible = {}
    for name in sorted(names):
        bank = _read("Bank Account", name)
        if (
            bank.get("disabled")
            or bank.get("is_company_account")
            or bank.get("party_type") != "Supplier"
            or bank.get("party") != supplier_name
        ):
            if name == default_bank:
                issues.append("supplier_default_bank_invalid")
            continue
        iban = _text(bank.get("iban"))
        if iban:
            eligible[name] = iban

    if default_bank:
        iban = eligible.get(default_bank)
        if not iban:
            if "supplier_default_bank_invalid" not in issues:
                issues.append("supplier_default_bank_invalid")
            return None
        return iban

    if len(eligible) == 1:
        return next(iter(eligible.values()))
    if len(eligible) > 1:
        issues.append("supplier_bank_account_ambiguous")
    return None


def _payment_review(doc, supplier_name, supplier, issues):
    invoice_iban = _text(doc.get("iban")) if doc.doctype == "Purchase Invoice" else ""
    supplier_iban = _text(supplier.get("iban"))
    method = (
        _text(doc.get("payment_type"))
        if doc.doctype == "Purchase Invoice"
        else _text(supplier.get("default_payment_method"))
    ) or _text(supplier.get("default_payment_method")) or "IBAN"

    if method not in ("IBAN", "ESR", "SEPA"):
        issues.append("supplier_payment_method_missing_or_invalid")
        return

    participant = _text(doc.get("esr_participation_number")) if doc.doctype == "Purchase Invoice" else ""
    participant = participant or _text(supplier.get("esr_participation_number"))
    reference = _text(doc.get("esr_reference_number")) if doc.doctype == "Purchase Invoice" else ""
    source_iban = invoice_iban or supplier_iban

    # Match ERPNextSwiss payment resolution: a QR-IBAN is an ESR route even
    # when the invoice form did not run its client-side supplier defaults.
    if _is_qr_iban(source_iban):
        method = "ESR"
        participant = source_iban
    elif method == "ESR" and invoice_iban and (
        _is_qr_iban(participant) or _is_qr_iban(supplier_iban)
    ):
        # An explicit normal invoice IBAN overrides an ESR QR-IBAN default.
        method = "IBAN"
        participant = ""
    elif method == "ESR" and not participant and _is_qr_iban(supplier_iban):
        participant = supplier_iban

    if method == "ESR":
        if not participant:
            issues.append("supplier_esr_participant_missing")
        elif _is_qr_iban(participant) and not _valid_iban(participant):
            issues.append("supplier_esr_participant_invalid")

        if doc.doctype == "Purchase Invoice":
            if not reference:
                issues.append("invoice_esr_reference_missing")
            elif _is_qr_iban(participant) and not _valid_qr_reference(reference):
                issues.append("invoice_qr_reference_invalid")
        return

    effective_iban = invoice_iban or supplier_iban
    if not effective_iban:
        effective_iban = _supplier_bank_iban(supplier_name, supplier, issues) or ""
    if not effective_iban:
        issues.append("supplier_payment_details_need_review")
    elif not _valid_iban(effective_iban):
        issues.append("supplier_iban_invalid")


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

        selected = doc.get("supplier_address") if doc.doctype == "Purchase Invoice" else None
        primary, complete_addresses = _supplier_addresses(supplier_name, supplier, selected)
        if not complete_addresses:
            issues.append("supplier_address_missing_or_incomplete")
        if primary and primary not in complete_addresses:
            issues.append("supplier_primary_address_invalid")
        if doc.doctype == "Purchase Invoice" and selected not in complete_addresses:
            issues.append("invoice_supplier_address_missing_or_invalid")

        _payment_review(doc, supplier_name, supplier, issues)
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


def intake_blocker(review):
    if review and not review["complete"]:
        return {
            "success": False,
            "error_type": "supplier_master_data_incomplete",
            "error": "Purchase Invoice creation blocked: required supplier address or payment data is missing or unverifiable.",
            "created": False,
            "submitted": False,
            "import_complete": False,
            "master_data_review": review,
            "suggestion": "Complete and verify the source-backed supplier address and payment details, then retry. Do not invent an IBAN, ESR participant number, or reference.",
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

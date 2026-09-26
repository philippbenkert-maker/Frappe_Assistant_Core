"""Offline regression tests. No bench, credentials or business writes required."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1] / "frappe_assistant_core"
IBAN = "CH9300762011623852957"  # Public format example, not a production account.
QR_IBAN = "CH98 3000 5248 2100 1701 C"  # Public example from the ISO 20022 test cases.


def make_qr_reference(prefix="12345678901234567890123456"):
    carry = 0
    lookup = (0, 9, 4, 6, 8, 2, 7, 1, 3, 5)
    for digit in prefix:
        carry = lookup[(carry + int(digit)) % 10]
    return prefix + str((10 - carry) % 10)


class Doc(SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def as_dict(self):
        return vars(self).copy()


def load_module(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def env(monkeypatch):
    frappe = ModuleType("frappe")
    frappe.session = SimpleNamespace(user="test@example.invalid")
    frappe._ = lambda text: text
    frappe.MandatoryError = type("MandatoryError", (Exception,), {})
    frappe.has_permission = Mock(return_value=True)
    frappe.db = SimpleNamespace(exists=Mock(return_value=True))
    frappe.log_error = Mock()
    frappe.get_meta = Mock(return_value=SimpleNamespace(fields=[], is_submittable=True))
    rows = {}
    frappe.get_doc = Mock(side_effect=lambda dt, name: Doc(**rows[(dt, name)]))

    def get_list(dt, **kwargs):
        assert kwargs["limit_page_length"] == 101
        filters = kwargs["filters"]
        if dt == "Address":
            assert filters == [["Dynamic Link", "link_doctype", "=", "Supplier"],
                               ["Dynamic Link", "link_name", "=", "SUP-1"]]
        else:
            assert filters == {"party_type": "Supplier", "party": "SUP-1"}
        return [{"name": name} for kind, name in rows if kind == dt]

    frappe.get_list = Mock(side_effect=get_list)
    security = ModuleType("frappe_assistant_core.core.security_config")
    security.validate_document_access = Mock(return_value={"success": True, "role": "System Manager"})
    security.filter_sensitive_fields = lambda doc, dt, role: doc
    security.SENSITIVE_FIELDS = {}
    security.ADMIN_ONLY_FIELDS = {}
    utils = ModuleType("frappe.utils")
    utils.validate_iban = Mock(side_effect=lambda value: value.replace(" ", "") == IBAN)
    base = ModuleType("frappe_assistant_core.core.base_tool")
    base.BaseTool = type("BaseTool", (), {})
    for name, module in [("frappe", frappe), ("frappe.utils", utils),
                         (security.__name__, security), (base.__name__, base)]:
        monkeypatch.setitem(sys.modules, name, module)
    review = load_module(monkeypatch, "frappe_assistant_core.core.supplier_master_data",
                         "core/supplier_master_data.py")
    return SimpleNamespace(frappe=frappe, security=security, utils=utils,
                           rows=rows, review=review, monkeypatch=monkeypatch)


def complete_supplier(env):
    env.rows[("Supplier", "SUP-1")] = {
        "name": "SUP-1",
        "iban": IBAN,
        "default_payment_method": "IBAN",
        "supplier_primary_address": "ADDR-1",
    }
    env.rows[("Address", "ADDR-1")] = {
        "name": "ADDR-1", "address_line1": "Example 1", "city": "Example City", "country": "Switzerland",
        "pincode": "8000",
        "links": [{"link_doctype": "Supplier", "link_name": "SUP-1"}],
    }
    return Doc(doctype="Purchase Invoice", supplier="SUP-1", supplier_address="ADDR-1", payment_type="IBAN")


def test_historical_name_only_supplier_and_iban_in_remarks_are_incomplete(env):
    env.rows[("Supplier", "SUP-1")] = {"name": "SUP-1"}
    invoice = Doc(doctype="Purchase Invoice", supplier="SUP-1", remarks="QR-IBAN " + IBAN)
    before = json.dumps(env.rows[("Supplier", "SUP-1")])
    result = env.review.review_supplier_master_data(invoice)
    assert result["status"] == "needs_review"
    assert "supplier_address_missing_or_incomplete" in result["issues"]
    assert "supplier_payment_details_need_review" in result["issues"]
    assert env.review.submission_blocker(result)["success"] is False
    assert IBAN not in json.dumps(result)
    assert json.dumps(env.rows[("Supplier", "SUP-1")]) == before


def test_linked_address_counts_without_primary_shortcut(env):
    invoice = complete_supplier(env)
    result = env.review.review_supplier_master_data(invoice)
    assert result["complete"] is True
    assert result["status"] == "structurally_complete"
    assert result["payment_authorized"] is False
    assert env.review.submission_blocker(result) is None
    env.utils.validate_iban.assert_called_once_with(IBAN)


@pytest.mark.parametrize("change", [
    {"address_line1": " "}, {"city": ""}, {"country": None}, {"pincode": ""}, {"disabled": 1},
    {"links": [{"link_doctype": "Customer", "link_name": "SUP-1"}]},
    {"links": [{"link_doctype": "Supplier", "link_name": "SOMEONE-ELSE"}]},
])
def test_incomplete_disabled_or_foreign_address_never_passes(env, change):
    invoice = complete_supplier(env)
    env.rows[("Address", "ADDR-1")].update(change)
    result = env.review.review_supplier_master_data(invoice)
    assert not result["complete"]
    assert "invoice_supplier_address_missing_or_invalid" in result["issues"]


def test_invoice_must_select_its_linked_address(env):
    invoice = complete_supplier(env)
    invoice.supplier_address = None
    result = env.review.review_supplier_master_data(invoice)
    assert result["issues"] == ["invoice_supplier_address_missing_or_invalid"]


def test_supplier_owned_bank_account_counts_without_supplier_iban(env):
    invoice = complete_supplier(env)
    env.rows[("Supplier", "SUP-1")].pop("iban")
    env.rows[("Bank Account", "BANK-1")] = {
        "name": "BANK-1", "party_type": "Supplier", "party": "SUP-1", "iban": IBAN,
    }
    assert env.review.review_supplier_master_data(invoice)["complete"]


@pytest.mark.parametrize("change", [{"disabled": 1}, {"is_company_account": 1},
                                   {"party": "FOREIGN"}, {"party_type": "Customer"}])
def test_invalid_default_bank_is_blocked_when_it_is_the_payment_source(env, change):
    invoice = complete_supplier(env)
    env.rows[("Supplier", "SUP-1")].pop("iban")
    env.rows[("Supplier", "SUP-1")]["default_bank_account"] = "BANK-1"
    env.rows[("Bank Account", "BANK-1")] = {
        "name": "BANK-1", "party_type": "Supplier", "party": "SUP-1", "iban": IBAN, **change,
    }
    result = env.review.review_supplier_master_data(invoice)
    assert "supplier_default_bank_invalid" in result["issues"]
    assert not result["complete"]


def test_invalid_iban_is_not_treated_as_complete(env):
    invoice = complete_supplier(env)
    env.rows[("Supplier", "SUP-1")]["iban"] = "invalid"
    result = env.review.review_supplier_master_data(invoice)
    assert result["issues"] == ["supplier_iban_invalid"]


@pytest.mark.parametrize("failure", ["permission", "query", "truncated"])
def test_read_failures_are_unverifiable_not_missing_or_success(env, failure):
    invoice = complete_supplier(env)
    if failure == "permission":
        env.security.validate_document_access.return_value = {"success": False}
    elif failure == "query":
        env.frappe.get_list.side_effect = RuntimeError("private connection detail")
    else:
        env.frappe.get_list.side_effect = None
        env.frappe.get_list.return_value = [{"name": "many"}] * 101
    result = env.review.review_supplier_master_data(invoice)
    assert result["status"] == "unverifiable"
    assert not result["complete"]
    assert "private connection detail" not in json.dumps(result)


def test_non_iban_payment_requires_manual_review_not_fabrication(env):
    invoice = complete_supplier(env)
    env.rows[("Supplier", "SUP-1")].pop("iban")
    invoice.payment_type = "SEPA"
    assert "supplier_payment_details_need_review" in env.review.review_supplier_master_data(invoice)["issues"]


def test_esr_invoice_can_be_complete_without_an_invoice_iban(env):
    invoice = complete_supplier(env)
    env.rows[("Supplier", "SUP-1")].pop("iban")
    env.rows[("Supplier", "SUP-1")]["default_payment_method"] = "ESR"
    env.rows[("Supplier", "SUP-1")]["esr_participation_number"] = "010123456"
    invoice.payment_type = "ESR"
    invoice.esr_reference_number = "12345678901234567890"

    result = env.review.review_supplier_master_data(invoice)

    assert result["complete"] is True
    assert "invoice_esr_reference_missing" not in result["issues"]
    assert "supplier_esr_participant_missing" not in result["issues"]


def test_esr_invoice_without_reference_is_blocked_even_when_supplier_has_iban(env):
    invoice = complete_supplier(env)
    invoice.payment_type = "ESR"
    invoice.esr_reference_number = ""
    env.rows[("Supplier", "SUP-1")]["default_payment_method"] = "ESR"
    env.rows[("Supplier", "SUP-1")]["esr_participation_number"] = "010123456"

    result = env.review.review_supplier_master_data(invoice)

    assert "invoice_esr_reference_missing" in result["issues"]
    assert env.review.intake_blocker(result)["created"] is False


def test_qr_iban_requires_valid_qr_reference(env):
    invoice = complete_supplier(env)
    invoice.payment_type = "ESR"
    invoice.iban = QR_IBAN
    invoice.esr_reference_number = make_qr_reference()
    env.rows[("Supplier", "SUP-1")]["esr_participation_number"] = "010123456"
    env.utils.validate_iban.side_effect = None
    env.utils.validate_iban.return_value = True

    assert env.review.review_supplier_master_data(invoice)["complete"] is True

    invoice.esr_reference_number = make_qr_reference()[:-1] + "0"
    result = env.review.review_supplier_master_data(invoice)
    assert "invoice_qr_reference_invalid" in result["issues"]


def test_multiple_bank_accounts_without_default_are_not_chosen_arbitrarily(env):
    invoice = complete_supplier(env)
    env.rows[("Supplier", "SUP-1")].pop("iban")
    for name in ("BANK-1", "BANK-2"):
        env.rows[("Bank Account", name)] = {
            "name": name, "party_type": "Supplier", "party": "SUP-1", "iban": IBAN,
        }

    result = env.review.review_supplier_master_data(invoice)

    assert "supplier_bank_account_ambiguous" in result["issues"]
    assert not result["complete"]


def test_prepare_uses_verified_primary_address_and_supplier_payment_method(env):
    complete_supplier(env)
    invoice = Doc(doctype="Purchase Invoice", supplier="SUP-1")

    env.review.prepare_purchase_invoice_defaults(invoice)

    assert invoice.supplier_address == "ADDR-1"
    assert invoice.payment_type == "IBAN"
    assert env.review.review_supplier_master_data(invoice)["complete"] is True


def test_prepare_does_not_guess_between_multiple_supplier_addresses(env):
    invoice = complete_supplier(env)
    invoice.supplier_address = None
    env.rows[("Supplier", "SUP-1")].pop("supplier_primary_address")
    env.rows[("Address", "ADDR-2")] = {
        "name": "ADDR-2", "address_line1": "Example 2", "city": "Example City", "country": "Switzerland",
        "pincode": "8000", "links": [{"link_doctype": "Supplier", "link_name": "SUP-1"}],
    }

    env.review.prepare_purchase_invoice_defaults(invoice)

    assert invoice.supplier_address is None
    assert "invoice_supplier_address_missing_or_invalid" in env.review.review_supplier_master_data(invoice)["issues"]


def test_masked_bank_field_is_unverifiable_instead_of_invalid(env):
    invoice = complete_supplier(env)
    env.rows[("Supplier", "SUP-1")]["iban"] = "***RESTRICTED***"
    assert env.review.review_supplier_master_data(invoice)["status"] == "unverifiable"


def test_missing_supplier_has_actionable_next_step(env):
    result = env.review.review_supplier_master_data(Doc(doctype="Purchase Invoice"))
    assert result["issues"] == ["supplier_missing"]
    assert result["next_steps"]


def document_harness(env, doctype):
    doc = Doc(doctype=doctype, name="SUP-1" if doctype == "Supplier" else "INV-1", docstatus=0,
              owner="test", creation="2026-01-01", modified="2026-01-01", modified_by="test")
    doc.insert = Mock()
    doc.submit = Mock(side_effect=lambda: setattr(doc, "docstatus", 1))
    doc.save = Mock()
    doc.reload = Mock()
    doc.run_method = Mock()
    env.frappe.new_doc = Mock(return_value=doc)
    reader = env.frappe.get_doc.side_effect
    env.frappe.get_doc.side_effect = lambda dt, name: doc if dt == "Purchase Invoice" else reader(dt, name)
    return doc


def document_tool(env, filename, classname):
    module = load_module(env.monkeypatch, "supplier_test_" + filename,
                         "plugins/core/tools/" + filename + ".py")
    return getattr(module, classname)()


@pytest.mark.parametrize("validate_only", [False, True])
def test_create_and_submit_blocks_before_any_insert(env, validate_only):
    env.rows[("Supplier", "SUP-1")] = {"name": "SUP-1"}
    doc = document_harness(env, "Purchase Invoice")
    tool = document_tool(env, "create_document", "DocumentCreate")
    result = tool.execute({"doctype": "Purchase Invoice", "submit": True, "validate_only": validate_only,
                           "data": {"supplier": "SUP-1", "remarks": IBAN}})
    assert result["success"] is False
    assert result["submitted"] is False
    doc.insert.assert_not_called()
    doc.submit.assert_not_called()


def test_incomplete_draft_intake_is_blocked_before_insert(env):
    env.rows[("Supplier", "SUP-1")] = {"name": "SUP-1"}
    doc = document_harness(env, "Purchase Invoice")
    result = document_tool(env, "create_document", "DocumentCreate").execute(
        {"doctype": "Purchase Invoice", "data": {"supplier": "SUP-1"}}
    )
    assert result["success"] is False
    assert result["error_type"] == "supplier_master_data_incomplete"
    assert result["created"] is False
    assert result["import_complete"] is False
    assert result["master_data_review"]["complete"] is False
    doc.insert.assert_not_called()
    doc.submit.assert_not_called()


def test_complete_draft_intake_is_saved_with_verified_defaults(env):
    complete_supplier(env)
    doc = document_harness(env, "Purchase Invoice")
    result = document_tool(env, "create_document", "DocumentCreate").execute(
        {"doctype": "Purchase Invoice", "data": {"supplier": "SUP-1"}}
    )

    assert result["success"] is True
    assert result["master_data_review"]["complete"] is True
    assert doc.supplier_address == "ADDR-1"
    assert doc.payment_type == "IBAN"
    assert result["defaults_applied"] == {"payment_type": "IBAN", "supplier_address": "ADDR-1"}
    assert not getattr(doc, "iban", None)
    doc.insert.assert_called_once()


def test_supplier_is_a_real_master_record_not_a_submittable_draft(env):
    env.rows[("Supplier", "SUP-1")] = {"name": "SUP-1"}
    doc = document_harness(env, "Supplier")
    result = document_tool(env, "create_document", "DocumentCreate").execute(
        {"doctype": "Supplier", "data": {"supplier_name": "Example Supplier"}}
    )
    assert result["success"] is True
    assert result["import_complete"] is False
    assert result["can_submit"] is False
    assert "draft" not in result["message"]
    assert result["master_data_review"]["issues"]
    doc.insert.assert_called_once()


@pytest.mark.parametrize("route", ["create_docstatus", "update_docstatus", "submit"])
def test_alternate_submission_routes_cannot_skip_incomplete_master_check(env, route):
    env.rows[("Supplier", "SUP-1")] = {"name": "SUP-1"}
    doc = document_harness(env, "Purchase Invoice")
    doc.supplier = "SUP-1"
    if route == "create_docstatus":
        tool = document_tool(env, "create_document", "DocumentCreate")
        result = tool.execute({"doctype": "Purchase Invoice", "data": {"supplier": "SUP-1", "docstatus": 1}})
    elif route == "update_docstatus":
        env.frappe.get_meta.return_value.istable = 0
        tool = document_tool(env, "update_document", "DocumentUpdate")
        result = tool.execute({"doctype": "Purchase Invoice", "name": "INV-1", "data": {"docstatus": 1}})
    else:
        tool = document_tool(env, "submit_document", "DocumentSubmit")
        result = tool.execute({"doctype": "Purchase Invoice", "name": "INV-1"})
    assert result["success"] is False
    assert result["error_type"] == "supplier_master_data_incomplete"
    doc.insert.assert_not_called()
    doc.save.assert_not_called()
    doc.submit.assert_not_called()


def test_complete_master_allows_authorized_create_submit(env):
    complete_supplier(env)
    doc = document_harness(env, "Purchase Invoice")
    result = document_tool(env, "create_document", "DocumentCreate").execute({
        "doctype": "Purchase Invoice", "submit": True,
        "data": {"supplier": "SUP-1", "supplier_address": "ADDR-1"},
    })
    assert result["success"] is True
    assert result["submitted"] is True
    assert result["master_data_review"]["payment_authorized"] is False
    doc.insert.assert_called_once()
    doc.submit.assert_called_once()


def test_unrelated_doctypes_do_not_query_supplier_data(env):
    doc = document_harness(env, "ToDo")
    result = document_tool(env, "create_document", "DocumentCreate").execute(
        {"doctype": "ToDo", "data": {"description": "Unrelated task"}}
    )
    assert result["success"]
    assert "master_data_review" not in result
    env.frappe.get_list.assert_not_called()
    doc.insert.assert_called_once()

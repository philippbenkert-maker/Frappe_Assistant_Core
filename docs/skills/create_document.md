# How to Use create_document

## Overview

The `create_document` tool creates new Frappe documents (records). It handles field validation, mandatory field checks, and permission verification automatically.

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `doctype` | string | **Yes** | — | Exact DocType name |
| `data` | object | **Yes** | — | Field values as key-value pairs |
| `submit` | boolean | No | `false` | Submit after creation (for submittable DocTypes) |
| `validate_only` | boolean | No | `false` | Validate without saving — use to test data format |

## Response Format

```json
{
  "success": true,
  "result": {
    "success": true,
    "doctype": "ToDo",
    "name": "TODO-00001",
    "data": { ... full document ... },
    "message": "ToDo 'TODO-00001' created successfully"
  }
}
```

## Best Practices

### Supplier invoice intake

Creating a Supplier is only one step of invoice intake. Read the complete source,
including the payment section, and resolve the supplier identity first. Store a
complete Address with a Supplier Dynamic Link and select it as `supplier_address`
on the invoice. Store verified bank details in `Supplier.iban` (where available)
or a supplier-owned Bank Account; an IBAN in `remarks` is not master data.

Inspect `master_data_review` after Supplier or Purchase Invoice operations.
`success=true` confirms the individual write, not a complete import. Supplier
records are not submittable drafts. Missing data must be reported as open work.
Read back the linked records before reporting completion. Never fabricate bank
details, and never silently replace an existing account based on OCR.

Purchase Invoice submission is blocked before insertion when the master-data
review is incomplete or unverifiable. Save a draft for unresolved intake; cash,
card, non-IBAN and changed-bank cases need manual review. Structural completeness
does not authorize payment or prove that an account matches the source invoice.
Do not route around a blocker using another tool.

### General document creation

1. **Check required fields first** — use `get_doctype_info` to see mandatory fields before creating.
2. **Use `validate_only: true` first** — test your data structure without actually creating the document.
3. **Link fields expect the `name` (ID)** — not the display title. Use `search_link` to find valid values.
4. **Don't set auto-generated fields** — `name`, `creation`, `modified`, `owner` are set automatically.
5. **Handle naming series** — DocTypes with naming series auto-generate names; don't pass `name` unless it uses manual naming.
6. **Use `submit: true` carefully** — only when explicitly requested. Creates and submits in one step.

## Common Patterns

### Simple document
```json
{
  "doctype": "ToDo",
  "data": {
    "description": "Follow up with client",
    "allocated_to": "user@example.com",
    "priority": "Medium",
    "date": "2024-06-15"
  }
}
```

### Document with child table
```json
{
  "doctype": "Sales Invoice",
  "data": {
    "customer": "Grant Plastics Ltd.",
    "items": [
      {
        "item_code": "ITEM-001",
        "qty": 5,
        "rate": 100
      }
    ]
  }
}
```

### Validate before creating
```json
{
  "doctype": "Customer",
  "data": {
    "customer_name": "New Corp",
    "customer_type": "Company"
  },
  "validate_only": true
}
```

### Create and submit in one step
```json
{
  "doctype": "Journal Entry",
  "data": { ... },
  "submit": true
}
```

## Edge Cases

- **Submittable DocTypes** are created in Draft state (`docstatus=0`) by default — use `submit: true` or `submit_document` tool separately.
- **Mandatory fields** that are missing cause a validation error — check with `get_doctype_info` first.
- **Unique constraints** — if a field has `unique=1`, duplicate values will fail.
- **Permission errors** — the current user must have "create" permission on the DocType.
- **Default values** — fields with defaults are auto-populated if not specified.
- **Child table rows** — pass as arrays of objects under the child table fieldname.

# Permission-aware document counts on Frappe 16

## Reproduced failure

The installed `list_documents` returned ten private project files together
with `total_count: 0` and `has_more: false`. Frappe 16.56.0 rejects the SQL
string `count(name) as count`; the tool swallowed that exception and replaced
it with an empty result. The same failure affected the document-ingress list.

The current Frappe API accepts `{"COUNT": "name", "as": "count"}` and returned
the correct count under the same ordinary authenticated ERP user. Frappe 15
retains the existing legacy string query. Both paths use `frappe.get_list`
with the identical scoped filters and `ignore_permissions=False`. There is
no `get_all`, SQL or privileged count fallback.

## Behaviour

- Frappe 16 uses the structured count expression.
- Valid zero, complete results and limited results remain distinguishable.
- Failed, missing, malformed or contradictory counts return `total_count:
  null`, `has_more: null`, `count_status: "unavailable"` and a clear message
  that the returned rows must not be treated as a complete list.
- A successful count returns `count_status: "verified"`.
- User scoping and submitted-document defaults apply to both queries.

## Evidence and release boundary

Before the fix, the offline regression produced 12 failures and 3 passes.
Afterwards all 54 standalone tests under `tests/` passed, including 15 count
regressions. The existing Bench count tests were updated for v15/v16 syntax;
their full Bench suite has not been run locally.

The exact committed candidate module was additionally executed in memory against the
live Frappe 16.56.0 read APIs under the authenticated user's permissions.
All six checks passed: limited files (2 of 10), complete files (10 of 10),
document ingress (1), submitted invoice (1), empty query (0), and Guest denial.
The transaction was rolled back. No application files, settings, business
documents or permissions were changed, and no files were duplicated.

Candidate module SHA-256:
`62a01ed8a11cdf35f98e62bfc9065ded720343395846e31f7b3792aabd765d69`.

This read-only probe is not a deployment or a substitute for the shared
release's source-integrity, permission, restore and live-activation gates.
The MCP client still runs the previous installed code until that release.
The fix has no schema or data migration and changes no tool input contract.

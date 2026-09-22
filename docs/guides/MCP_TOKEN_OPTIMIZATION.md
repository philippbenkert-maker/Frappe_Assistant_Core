# MCP token and credit optimization

FAC applies token controls at the MCP boundary, so they also cover tools added
by plugins or external Frappe apps. The default `Lean` profile is intended for
ChatGPT users who perform normal search, document lookup, reporting, and
approval work.

## Upgrade and deployment

After updating the app, run the normal site migration:

```bash
bench --site <site-name> migrate
```

The v2.6 patch is idempotent. It reloads the settings and audit DocTypes,
creates the User profile fields, switches existing sites to the `Lean` tool
profile and `replace` skill mode, and preserves later administrator changes.
The post-migration hook then refreshes tool discovery and system skills.

## Recommended configuration

Open **Assistant Core Settings → Token & Credit Optimization**. The shipped
defaults are:

| Setting | Default | Purpose |
| --- | ---: | --- |
| Tool profile | Lean | Exposes seven common tools instead of the complete catalog |
| Maximum exposed tools | 10 | Bounds Lean, Standard, and Custom profiles |
| Maximum result characters | 12,000 | Replaces oversized output with a preview and continuation id |
| Maximum preview items | 20 | Limits arrays in compacted previews |
| Result lifetime | 900 seconds | Retains full output briefly for paging |
| Maximum list rows | 50 | Limits document and analysis queries |
| Maximum report preview rows | 20 | Limits report output before paging |
| Maximum OCR pages | 5 | Avoids accidentally processing large PDFs |
| Maximum Python output | 16,000 characters | Prevents console output flooding |

Keep `Use Compact JSON` and `Use Concise Tool Descriptions` enabled. Detailed
tool instructions remain available through FAC skills on demand.

## Per-user profiles

Open a **User** record and set **Assistant MCP Tool Profile**:

- `Inherit` uses the site default.
- `Lean` provides search, fetch, document reading, compact reports, approvals,
  and result paging.
- `Standard` additionally provides document create/update and workflow actions.
- `Full` exposes every otherwise permitted tool and should be temporary.
- `Custom` uses the comma- or newline-separated names in **Assistant MCP
  Allowed Tools**. FAC automatically retains `get_result_page`.

For a high-consumption user such as Adrian, use `Lean` unless write operations
are genuinely needed. Use `Standard` for those sessions and return the user to
`Lean` afterwards.

Profiles only reduce the tools advertised to the model; normal Frappe roles,
permissions, plugin status, and individual tool configuration still apply.

## Large results

Every MCP result passes through the central response budget. When a result is
too large, the response contains `_mcp_continuation` with a `result_id`,
available list paths, and arguments for `get_result_page`. Cached results are
bound to the calling user and expire automatically.

Use `business_report` for report discovery, filter requirements, and compact
execution. It returns a numeric summary and a bounded row preview, with paging
when additional rows are available.

## Monitoring

Each Assistant Audit Log records original bytes, transmitted bytes, estimated
MCP tokens, and whether output was compacted. The admin usage endpoint also
returns a seven-day per-user aggregate in `mcp_usage_last_7_days`.

The estimate is useful for comparison and anomaly detection. Provider usage
data remains authoritative for billing and workspace credits.

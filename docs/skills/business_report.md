# Business reports with a small model context

`business_report` combines report discovery, filter inspection, and execution in one tool.

## Actions

- `discover`: Find accessible reports. Add `query`, `module`, or `report_type` to narrow the result.
- `requirements`: Read the exact filter contract for one report before running it.
- `run`: Execute a report with explicit `filters`.

Run responses contain a bounded row preview, total row count, and numeric summaries. When more rows exist, use the returned `result_id` and `continuation` arguments with `get_result_page`. Do not rerun the complete report merely to obtain the next page.

If `run` receives invalid or missing filters, its error response includes the report requirements so the next call can correct the filters without another discovery call.

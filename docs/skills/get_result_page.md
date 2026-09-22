# Continue a compacted MCP result

Large tool responses include `_mcp_continuation` with a user-bound `result_id`, available list paths, and an expiry time.

Call `get_result_page` with:

- `result_id`: the opaque identifier from the earlier response;
- `path`: one of `available_list_paths` when a specific list is needed;
- `offset`: the preceding response's `next_offset`;
- `limit`: normally 20 or less.

Continue only while `has_more` is true. Results are temporary and can only be read by the user who created them.

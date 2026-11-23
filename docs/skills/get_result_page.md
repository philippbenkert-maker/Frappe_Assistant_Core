# Continue a compacted MCP result

Large tool responses include `_mcp_continuation` with a user-bound `result_id`, available list paths, and an expiry time.

Call `get_result_page` with:

- `result_id`: the opaque identifier from the earlier response;
- `path`: one of `available_list_paths` when a specific list is needed;
- `offset`: the preceding response's `next_offset`;
- `limit`: normally 20 or less.
- `as_text`: send `true` whenever the previous page returned `as_text: true`.

A page can return fewer items than requested to stay within the result budget. If an individual row cannot fit, the result starts a text view of the entire selected list at offset zero. Join the `text` chunks in order, then parse the full JSON. When a path itself is too long, `path: "$"` with `as_text: true` reads the whole retained result.

Continue while `has_more` is true. Results are temporary and belong to the user who created them.

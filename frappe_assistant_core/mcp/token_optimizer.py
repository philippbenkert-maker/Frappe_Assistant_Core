# Frappe Assistant Core - AI Assistant integration for Frappe Framework
# Copyright (C) 2025 Paul Clinton
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Central MCP token-budget and tool-profile enforcement.

The optimizer intentionally sits at the MCP boundary.  That makes the limits
apply to built-in tools, tools supplied by plugins, and tools added by other
Frappe apps without requiring every implementation to remember token hygiene.
All database/cache imports are lazy so the pure selection/compaction helpers
remain straightforward to unit-test outside a running Frappe site.
"""

import json
import math
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LEAN_TOOL_ORDER = (
    "search",
    "fetch",
    "list_documents",
    "get_document",
    "business_report",
    "get_pending_approvals",
    "get_result_page",
)

STANDARD_TOOL_ORDER = LEAN_TOOL_ORDER + (
    "create_document",
    "update_document",
    "run_workflow",
)

PROFILE_TOOL_ORDER = {
    "Lean": LEAN_TOOL_ORDER,
    "Standard": STANDARD_TOOL_ORDER,
}


@dataclass
class MCPOptimizationConfig:
    enabled: bool = True
    profile: str = "Lean"
    custom_tools: Tuple[str, ...] = ()
    max_tools: int = 10
    max_result_chars: int = 12_000
    max_result_items: int = 20
    max_string_chars: int = 4_000
    result_ttl_seconds: int = 900
    compact_json: bool = True
    concise_descriptions: bool = True
    max_description_chars: int = 280
    max_list_rows: int = 50
    max_report_rows: int = 20
    max_ocr_pages: int = 5
    max_code_output_chars: int = 16_000


def _as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return bool(value)


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def parse_tool_list(value: Any) -> Tuple[str, ...]:
    """Parse JSON, comma-separated, or newline-separated tool names."""
    if not value:
        return ()
    if isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        text = str(value).strip()
        try:
            parsed = json.loads(text)
            candidates = parsed if isinstance(parsed, list) else re.split(r"[,\n]", text)
        except (TypeError, ValueError, json.JSONDecodeError):
            candidates = re.split(r"[,\n]", text)

    result = []
    seen = set()
    for candidate in candidates:
        name = str(candidate).strip()
        if name and name not in seen:
            seen.add(name)
            result.append(name)
    return tuple(result)


def get_optimization_config(user: Optional[str] = None) -> MCPOptimizationConfig:
    """Load site defaults plus optional per-user profile overrides.

    Missing fields are tolerated deliberately.  This keeps requests working
    while a rolling deployment is between code update and ``bench migrate``.
    """
    config = MCPOptimizationConfig()
    try:
        import frappe

        settings = frappe.get_single("Assistant Core Settings")
        config.enabled = _as_bool(getattr(settings, "mcp_token_optimization_enabled", None), config.enabled)
        config.profile = getattr(settings, "mcp_default_tool_profile", None) or config.profile
        config.max_tools = _as_int(getattr(settings, "mcp_max_tools", None), config.max_tools, 1, 100)
        config.max_result_chars = _as_int(
            getattr(settings, "mcp_max_result_chars", None), config.max_result_chars, 2_000, 200_000
        )
        config.max_result_items = _as_int(
            getattr(settings, "mcp_max_result_items", None), config.max_result_items, 1, 500
        )
        config.max_string_chars = _as_int(
            getattr(settings, "mcp_max_string_chars", None), config.max_string_chars, 500, 100_000
        )
        config.result_ttl_seconds = _as_int(
            getattr(settings, "mcp_result_ttl_seconds", None), config.result_ttl_seconds, 60, 86_400
        )
        config.compact_json = _as_bool(getattr(settings, "mcp_compact_json", None), config.compact_json)
        config.concise_descriptions = _as_bool(
            getattr(settings, "mcp_concise_descriptions", None), config.concise_descriptions
        )
        config.max_description_chars = _as_int(
            getattr(settings, "mcp_max_description_chars", None),
            config.max_description_chars,
            80,
            2_000,
        )
        config.max_list_rows = _as_int(
            getattr(settings, "mcp_max_list_rows", None), config.max_list_rows, 1, 1_000
        )
        config.max_report_rows = _as_int(
            getattr(settings, "mcp_max_report_rows", None), config.max_report_rows, 1, 1_000
        )
        config.max_ocr_pages = _as_int(
            getattr(settings, "mcp_max_ocr_pages", None), config.max_ocr_pages, 1, 200
        )
        config.max_code_output_chars = _as_int(
            getattr(settings, "mcp_max_code_output_chars", None),
            config.max_code_output_chars,
            1_000,
            1_000_000,
        )

        effective_user = user or getattr(getattr(frappe, "session", None), "user", None)
        if effective_user and effective_user != "Guest":
            try:
                values = frappe.db.get_value(
                    "User",
                    effective_user,
                    ["assistant_mcp_tool_profile", "assistant_mcp_allowed_tools"],
                    as_dict=True,
                )
                if values:
                    user_profile = values.get("assistant_mcp_tool_profile")
                    if user_profile and user_profile != "Inherit":
                        config.profile = user_profile
                    config.custom_tools = parse_tool_list(values.get("assistant_mcp_allowed_tools"))
            except Exception:
                # Custom fields may not exist until the migration has completed.
                pass
    except Exception:
        # Fallback defaults preserve MCP availability during install/migrate.
        pass

    if config.profile not in {"Lean", "Standard", "Full", "Custom"}:
        config.profile = "Lean"
    return config


def select_tool_metadata(
    tools: Sequence[Dict[str, Any]], config: MCPOptimizationConfig
) -> List[Dict[str, Any]]:
    """Return the deterministic, profile-specific tool subset.

    ``Full`` is an explicit escape hatch and therefore intentionally ignores
    ``max_tools``. Bounded profiles reserve a slot for the paging tool when
    it is available to the current user, since any tool can return a continuation.
    """
    if not config.enabled or config.profile == "Full":
        return list(tools)

    by_name = {tool.get("name"): tool for tool in tools if tool.get("name")}
    if config.profile == "Custom":
        order = config.custom_tools
    else:
        order = list(PROFILE_TOOL_ORDER.get(config.profile, LEAN_TOOL_ORDER))

    selected = [by_name[name] for name in order if name in by_name]
    selected = selected[: config.max_tools]
    if "get_result_page" in by_name and not any(tool["name"] == "get_result_page" for tool in selected):
        selected = selected[: config.max_tools - 1]
        selected.append(by_name["get_result_page"])
    return selected


def concise_description(description: str, max_chars: int = 280) -> str:
    """Shorten verbose tool prose while retaining its first useful sentences."""
    normalized = " ".join((description or "").split())
    if len(normalized) <= max_chars:
        return normalized

    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    kept = ""
    for sentence in sentences:
        candidate = sentence if not kept else f"{kept} {sentence}"
        if len(candidate) > max_chars:
            break
        kept = candidate
    if kept:
        return kept
    return normalized[: max_chars - 1].rstrip() + "…"


def compact_json_dumps(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))


def serialize_tool_result(value: Any, compact: bool) -> str:
    if isinstance(value, str):
        return value
    if compact:
        return compact_json_dumps(value)
    return json.dumps(value, default=str, ensure_ascii=False, indent=2)


def estimate_tokens_from_chars(char_count: int) -> int:
    """Conservative telemetry estimate; billing must use provider usage data."""
    return int(math.ceil(max(0, char_count) / 4.0))


def _compact_value(value: Any, max_items: int, max_string_chars: int, depth: int = 0) -> Any:
    if depth >= 12:
        return "[nested value omitted]"
    if isinstance(value, str):
        if len(value) <= max_string_chars:
            return value
        return value[:max_string_chars] + f"… [{len(value) - max_string_chars} chars omitted]"
    if isinstance(value, list):
        compacted = [
            _compact_value(item, max_items, max_string_chars, depth + 1) for item in value[:max_items]
        ]
        if len(value) > max_items:
            compacted.append({"_omitted_items": len(value) - max_items})
        return compacted
    if isinstance(value, tuple):
        return _compact_value(list(value), max_items, max_string_chars, depth)
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, max_items, max_string_chars, depth + 1)
            for key, item in value.items()
        }
    return value


def _list_paths(value: Any, prefix: str = "", depth: int = 0) -> List[Tuple[str, int]]:
    if depth > 8:
        return []
    if isinstance(value, list):
        return [(prefix or "$", len(value))] if value else []
    paths = []
    if isinstance(value, dict):
        for key, item in value.items():
            # A hint must round-trip through the resolver's dot notation.
            # Unaddressable object keys remain available in the full JSON text.
            if not isinstance(key, str) or not key or "." in key or (not prefix and key == "$"):
                continue
            child = f"{prefix}.{key}" if prefix else key
            paths.extend(_list_paths(item, child, depth + 1))
    return paths


def _cache_key(result_id: str) -> str:
    return f"fac:mcp:result:{result_id}"


def store_serialized_result(serialized: str, user: str, ttl_seconds: int) -> str:
    """Store a complete oversized result in the site cache for continuation."""
    import frappe

    result_id = uuid.uuid4().hex
    payload = compact_json_dumps({"user": user, "serialized": serialized})
    frappe.cache.set_value(_cache_key(result_id), payload, expires_in_sec=ttl_seconds)
    return result_id


def compact_tool_result(
    value: Any,
    user: str,
    config: MCPOptimizationConfig,
) -> Tuple[Any, Dict[str, Any]]:
    """Apply the configured response budget and retain a pageable full result."""
    serialized = serialize_tool_result(value, config.compact_json)
    original_chars = len(serialized)
    metrics = {
        "original_chars": original_chars,
        "original_bytes": len(serialized.encode("utf-8")),
        "transmitted_chars": original_chars,
        "transmitted_bytes": len(serialized.encode("utf-8")),
        "estimated_tokens": estimate_tokens_from_chars(original_chars),
        "truncated": False,
        "result_id": None,
    }
    if not config.enabled or original_chars <= config.max_result_chars:
        return value, metrics

    cached_json = compact_json_dumps(value) if isinstance(value, str) else serialized
    result_id = store_serialized_result(cached_json, user, config.result_ttl_seconds)
    max_items = config.max_result_items
    max_string_chars = config.max_string_chars
    preview = _compact_value(value, max_items, max_string_chars)
    paths = sorted(_list_paths(value), key=lambda item: item[1], reverse=True)[:10]

    continuation = {
        "truncated": True,
        "result_id": result_id,
        "original_chars": original_chars,
        "estimated_original_tokens": estimate_tokens_from_chars(original_chars),
        "expires_in_seconds": config.result_ttl_seconds,
        "available_list_paths": [{"path": path, "items": count} for path, count in paths],
        "next": {"tool": "get_result_page", "arguments": {"result_id": result_id}},
    }

    if isinstance(preview, dict):
        preview["_mcp_continuation"] = continuation
    else:
        preview = {"preview": preview, "_mcp_continuation": continuation}

    # Tighten recursively if unusually wide dictionaries still exceed budget.
    for _ in range(8):
        preview_text = serialize_tool_result(preview, config.compact_json)
        if len(preview_text) <= config.max_result_chars:
            break
        max_items = max(1, max_items // 2)
        max_string_chars = max(250, max_string_chars // 2)
        preview = _compact_value(value, max_items, max_string_chars)
        if isinstance(preview, dict):
            preview["_mcp_continuation"] = continuation
        else:
            preview = {"preview": preview, "_mcp_continuation": continuation}
    else:
        preview = {"_mcp_continuation": continuation}

    preview_text = serialize_tool_result(preview, config.compact_json)
    if len(preview_text) > config.max_result_chars:
        continuation.pop("available_list_paths")
        preview_text = serialize_tool_result(preview, config.compact_json)
    transmitted_chars = len(preview_text)
    metrics.update(
        {
            "transmitted_chars": transmitted_chars,
            "transmitted_bytes": len(preview_text.encode("utf-8")),
            "estimated_tokens": estimate_tokens_from_chars(transmitted_chars),
            "truncated": True,
            "result_id": result_id,
        }
    )
    return preview, metrics


def _resolve_path(value: Any, path: Optional[str]) -> Tuple[Any, str]:
    if not path or path == "$":
        list_paths = sorted(_list_paths(value), key=lambda item: item[1], reverse=True)
        path = list_paths[0][0] if list_paths else "$"
    if path == "$":
        return value, path

    current = value
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(f"Result path not found: {path}")
    return current, path


def _fit_result_page(build_page, length: int, config: Optional[MCPOptimizationConfig]) -> Dict[str, Any]:
    if config is None or not config.enabled:
        return build_page(length)

    def fits(page):
        return len(serialize_tool_result(page, config.compact_json)) <= config.max_result_chars

    # Check the complete page first. Its final offset metadata may be shorter
    # than intermediate pages once there is no further content.
    length = min(length, config.max_result_chars)
    page = build_page(length)
    if fits(page):
        return page

    best = build_page(0)
    if not fits(best):
        raise ValueError("Result path exceeds the page budget. Use path '$' with as_text to read the whole result.")

    low, high = 1, length - 1
    while low <= high:
        middle = (low + high) // 2
        page = build_page(middle)
        if fits(page):
            best = page
            low = middle + 1
        else:
            high = middle - 1
    return best


def read_result_page(
    result_id: str,
    user: str,
    path: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
    *,
    as_text: bool = False,
    config: Optional[MCPOptimizationConfig] = None,
) -> Dict[str, Any]:
    """Read a user-bound page that can be returned without another continuation."""
    import frappe

    cached = frappe.cache.get_value(_cache_key(result_id))
    if not cached:
        raise ValueError("Result expired or not found")
    if isinstance(cached, bytes):
        cached = cached.decode("utf-8")
    payload = json.loads(cached) if isinstance(cached, str) else cached
    if payload.get("user") != user:
        raise PermissionError("Result belongs to a different user")

    value = json.loads(payload["serialized"])
    target, resolved_path = (value, "$") if as_text and path == "$" else _resolve_path(value, path)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 20), 100))

    def offsets(length, total):
        last = offset + length
        return {
            "result_id": result_id,
            "path": resolved_path,
            "offset": offset,
            "has_more": last < total,
            "next_offset": last if last < total else None,
        }

    if isinstance(target, list) and not as_text:
        total = len(target)

        def list_page(length):
            return {**offsets(length, total), "limit": limit, "items": target[offset : offset + length],
                    "total_items": total}

        page = _fit_result_page(list_page, max(0, min(limit, total - offset)), config)
        if page["items"] or not page["has_more"]:
            return page

        # An individual row does not fit. Start a textual view of the same
        # retained list so the caller can still recover every original byte.
        offset = 0
        as_text = True

    text = target if isinstance(target, str) else compact_json_dumps(target)
    total = len(text)

    def text_page(length):
        page = {**offsets(length, total), "text": text[offset : offset + length], "total_chars": total}
        if as_text:
            page["as_text"] = True
        return page

    page = _fit_result_page(text_page, max(0, min(limit * 500, 20_000, total - offset)), config)
    if not page["text"] and page["has_more"]:
        raise ValueError("No text fits in the page budget. Use path '$' with as_text to read the whole result.")
    return page

def update_audit_metrics(tool_name: str, arguments: Dict[str, Any], metrics: Dict[str, Any]) -> None:
    """Attach MCP wire-size telemetry to the audit row created by BaseTool."""
    try:
        import frappe

        audit_name = getattr(frappe.local, "last_assistant_audit_log_name", None)
        if not audit_name:
            return
        input_chars = len(compact_json_dumps(arguments or {}))
        frappe.db.set_value(
            "Assistant Audit Log",
            audit_name,
            {
                "mcp_input_bytes": len(compact_json_dumps(arguments or {}).encode("utf-8")),
                "mcp_output_bytes": int(metrics.get("original_bytes", metrics.get("original_chars", 0))),
                "mcp_transmitted_bytes": int(
                    metrics.get("transmitted_bytes", metrics.get("transmitted_chars", 0))
                ),
                "mcp_estimated_tokens": estimate_tokens_from_chars(
                    input_chars + int(metrics.get("transmitted_chars", 0))
                ),
                "mcp_output_truncated": 1 if metrics.get("truncated") else 0,
            },
            update_modified=False,
        )
    except Exception:
        # Telemetry must never make a business tool fail.
        pass

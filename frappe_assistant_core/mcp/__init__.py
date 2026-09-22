"""
Frappe Assistant Core - Custom MCP Server Implementation

A streamlined MCP (Model Context Protocol) server implementation
specifically designed for Frappe Framework.

Based on the MCP specification with fixes for proper JSON serialization
and Frappe-specific optimizations.
"""

__all__ = ["MCPServer"]


def __getattr__(name):
    """Load the HTTP server lazily.

    Token/profile helpers can now be imported by migrations and lightweight
    diagnostics without importing Werkzeug before Frappe has initialized it.
    ``from frappe_assistant_core.mcp import MCPServer`` remains compatible.
    """
    if name == "MCPServer":
        from frappe_assistant_core.mcp.server import MCPServer

        return MCPServer
    raise AttributeError(name)

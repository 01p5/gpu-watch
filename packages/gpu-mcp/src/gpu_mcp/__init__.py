"""gpu-mcp — MCP stdio server for gpu-watch."""
from .tools import TOOLS, dispatch_tool, tools_descriptor

__all__ = ["TOOLS", "dispatch_tool", "tools_descriptor"]

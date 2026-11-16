"""t20_arch_mcp - Minimal MCP server for T20-Arch V10.0 (Tianzheng Architecture).

Bridges an MCP client (dsh) to a *running* AutoCAD instance that already has the
Tianzheng ARX modules loaded, so that native Tianzheng custom objects
(TCH_WALL, TCH_OPENING, ...) can be produced by issuing real Tianzheng commands.
"""

__version__ = "0.5.0"

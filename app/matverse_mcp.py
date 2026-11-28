"""Unified MatVerse stdio MCP: Bridge + local HTTP + PHOENIX + local browser."""
from __future__ import annotations

import atexit

from . import agent_mcp, local_web_mcp, local_browser_sessions

local_web_mcp.install(agent_mcp)
local_browser_sessions.install(agent_mcp)
atexit.register(local_browser_sessions.shutdown)

if __name__ == "__main__":
    agent_mcp.main()

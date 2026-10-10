# Claude Code aborts MCP tool calls after 60 seconds

Claude Code aborts an HTTP MCP tool call after 60 seconds unless the server's config sets "timeout" (milliseconds) or MCP_TOOL_TIMEOUT is set, which any tool that blocks longer outlasts.

Claude Code's HTTP MCP client (wrapFetchWithTimeout, seen in 2.1.295) gives every non-GET request to an MCP server the larger of the server's "timeout" key in .mcp.json and MCP_TOOL_TIMEOUT, else 60000 ms, then drops the connection, so the server's answer reaches nobody. A tool that waits for another agent (a long poll, a request with a wait) runs past 60 seconds by design, so a server with such a tool needs the key in its config entry, or the variable in the environment that starts Claude Code; `claude mcp add-json` accepts and stores the key.

This repository sets both: every arm's .mcp.json under coordbench/arms/ carries "timeout": 330000, above the longest wait any built-in arm allows (300 seconds), and drivers.CLAUDE_ENVIRONMENT in coordbench/drivers.py exports MCP_TOOL_TIMEOUT=330000 to each agent's container, for an arm author who rewrites a plugin's .mcp.json. A new arm's .mcp.json keeps the key; a new blocking tool stays under the value or raises it everywhere.

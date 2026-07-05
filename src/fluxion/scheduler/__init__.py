# Time- and quota-driven task scheduler for Fluxion.
#
# A standalone daemon (`fluxion-scheduler`) that watches the clock and provider
# quota windows, and fires sub-agent runs through the same SubagentRunner used
# by the MCP server and CLI. The web UI only reads its JSONL state.

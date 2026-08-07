"""analyst_agent: a real, Anthropic-backed example agent that joins a SMAC
workspace as a member -- answers when mentioned, and can be watched (inner
view) and talked to (direct chat) from one terminal.

This package is intentionally separate from the server: it is installed
from `examples/analyst_agent/requirements.txt`, never from the top-level
`pyproject.toml`, and imports nothing from `app`, `smac_cli`, or
`smac_mcp`. `smac_link.py` is its only dependency on SMAC's own wire
shapes.
"""

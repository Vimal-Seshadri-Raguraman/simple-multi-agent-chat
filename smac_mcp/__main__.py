"""Entry point: `python -m smac_mcp` serves the bridge over stdio;
`python -m smac_mcp create-agent` walks through creating a new agent
member and printing its one-time API key.
"""

import argparse
import os
import sys

from smac_mcp.api import SmacApi
from smac_mcp.create_agent import run_interactive
from smac_mcp.server import build_server


def _serve() -> None:
    """Read env, fail fast without SMAC_API_KEY, serve the bridge over stdio."""
    api_key = os.environ.get("SMAC_API_KEY")
    if not api_key:
        print(
            "SMAC_API_KEY is not set. Create an agent with "
            "`python -m smac_mcp create-agent` and set its key.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    url = os.environ.get("SMAC_URL", "http://127.0.0.1:8000")
    build_server(SmacApi(base_url=url, api_key=api_key)).run()


def main() -> None:
    """Dispatch: no subcommand serves the bridge; `create-agent` walks
    through creating a new agent member interactively."""
    parser = argparse.ArgumentParser(prog="python -m smac_mcp")
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "create-agent",
        help="Interactively create an agent member and print its one-time API key",
    )
    args = parser.parse_args()

    if args.command == "create-agent":
        run_interactive()
    else:
        _serve()


if __name__ == "__main__":
    main()

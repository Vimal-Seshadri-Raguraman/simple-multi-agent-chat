"""Entry point: `python -m smac_mcp` serves the bridge over stdio;
`python -m smac_mcp create-agent` walks through creating a new agent
member and printing its one-time API key; `python -m smac_mcp
build-bundle` packages the bridge as a `.mcpb` file for Claude Desktop.
"""

import argparse
import os
import sys

from smac_mcp.api import SmacApi
from smac_mcp.build_bundle import _DEFAULT_OUTPUT, build_bundle
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
    build_bundle_parser = subparsers.add_parser(
        "build-bundle",
        help="Package the bridge as a .mcpb file for Claude Desktop",
    )
    build_bundle_parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help=f"Output path for the bundle (default: {_DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if args.command == "create-agent":
        run_interactive()
    elif args.command == "build-bundle":
        output_path = build_bundle(args.output)
        print(f"Wrote {output_path}")
    else:
        _serve()


if __name__ == "__main__":
    main()

"""Entry point: `python -m smac_mcp` serves the bridge over stdio."""

import os
import sys

from smac_mcp.api import SmacApi
from smac_mcp.server import build_server


def main() -> None:
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


if __name__ == "__main__":
    main()

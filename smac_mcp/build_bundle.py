"""`build-bundle`: package the SMAC MCP bridge as a `.mcpb` (MCP Bundle) —
a zip containing `manifest.json` plus the `smac_mcp` package source, laid
out per the MCP Bundles spec (https://github.com/modelcontextprotocol/mcpb)
so Claude Desktop can install it, prompt for `smac_url`/`api_key`, and run
`python -m smac_mcp` with those values injected as `SMAC_URL`/`SMAC_API_KEY`.

This only packages the bridge's own source — third-party dependencies
(fastmcp, httpx, ...) are not bundled into the zip. The `python` on the
host's PATH must already have them installed (the same venv used to run
`python -m smac_mcp create-agent`/serve today). Full dependency bundling
(`server/lib` or `server/venv` per the spec) is future work.
"""

import zipfile
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_MANIFEST_PATH = _PACKAGE_DIR / "bundle" / "manifest.json"
_DEFAULT_OUTPUT = Path("dist") / "smac.mcpb"


def build_bundle(output_path: Path | str = _DEFAULT_OUTPUT) -> Path:
    """Write a `.mcpb` zip at `output_path`: `manifest.json` at the archive
    root, plus every `smac_mcp/*.py` source file under a `smac_mcp/`
    prefix (matching the manifest's `entry_point` and `PYTHONPATH`).

    Returns the resolved output path. Creates parent directories as
    needed; overwrites an existing file at `output_path`.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(_MANIFEST_PATH, arcname="manifest.json")
        for source_file in sorted(_PACKAGE_DIR.glob("*.py")):
            bundle.write(source_file, arcname=str(Path("smac_mcp") / source_file.name))

    return output_path

"""build_bundle: packages manifest.json + smac_mcp source into a .mcpb zip.

Schema-sanity tests only — asserts the manifest parses and declares what
Claude Desktop needs (per https://github.com/modelcontextprotocol/mcpb),
not a live Claude Desktop integration.
"""

import json
import zipfile
from pathlib import Path

from smac_mcp.build_bundle import build_bundle


def _read_manifest(bundle_path: Path) -> dict:
    with zipfile.ZipFile(bundle_path) as bundle:
        return json.loads(bundle.read("manifest.json"))


def test_build_bundle_writes_a_zip_at_the_given_path(tmp_path):
    output_path = tmp_path / "smac.mcpb"

    result = build_bundle(output_path)

    assert result == output_path
    assert output_path.is_file()
    assert zipfile.is_zipfile(output_path)


def test_manifest_parses_as_json(tmp_path):
    manifest = _read_manifest(build_bundle(tmp_path / "smac.mcpb"))

    assert manifest["name"] == "smac"
    assert "manifest_version" in manifest


def test_manifest_declares_both_user_config_fields(tmp_path):
    manifest = _read_manifest(build_bundle(tmp_path / "smac.mcpb"))

    user_config = manifest["user_config"]
    assert user_config["smac_url"]["type"] == "string"
    assert user_config["smac_url"]["default"] == "http://127.0.0.1:8000"
    assert user_config["api_key"]["sensitive"] is True
    assert user_config["api_key"]["required"] is True


def test_manifest_maps_user_config_into_env_vars(tmp_path):
    manifest = _read_manifest(build_bundle(tmp_path / "smac.mcpb"))

    env = manifest["server"]["mcp_config"]["env"]
    assert env["SMAC_URL"] == "${user_config.smac_url}"
    assert env["SMAC_API_KEY"] == "${user_config.api_key}"


def test_manifest_invokes_python_dash_m_smac_mcp(tmp_path):
    manifest = _read_manifest(build_bundle(tmp_path / "smac.mcpb"))

    mcp_config = manifest["server"]["mcp_config"]
    assert mcp_config["command"] == "python"
    assert mcp_config["args"] == ["-m", "smac_mcp"]


def test_bundle_contains_the_smac_mcp_package_source(tmp_path):
    output_path = build_bundle(tmp_path / "smac.mcpb")

    with zipfile.ZipFile(output_path) as bundle:
        names = bundle.namelist()

    assert "smac_mcp/__main__.py" in names
    assert "smac_mcp/server.py" in names
    assert "smac_mcp/api.py" in names


def test_build_bundle_creates_missing_parent_directories(tmp_path):
    output_path = tmp_path / "nested" / "dir" / "smac.mcpb"

    result = build_bundle(output_path)

    assert result == output_path
    assert output_path.is_file()


def test_build_bundle_default_output_is_dist_smac_mcpb():
    from smac_mcp.build_bundle import _DEFAULT_OUTPUT

    assert _DEFAULT_OUTPUT == Path("dist") / "smac.mcpb"

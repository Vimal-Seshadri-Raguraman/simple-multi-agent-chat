#!/usr/bin/env bash
# Rebuild-freshness gate (SMAC-85 Task 6, web spec §5's "fresh-build-
# committed check"): rebuilds the web bundle from `smac_web/src` and
# proves the freshly built output is byte-identical to the COMMITTED copy
# at `app/static/webui/`. The design system constitution's §8 trade (pip
# users get the UI without ever touching Node) only holds if that
# committed bundle is kept honest -- this is the final gate that verifies
# it, in the absence of a Node build step in CI.
#
# Run from anywhere; paths below are resolved relative to this script's
# own location, not the caller's cwd.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SMAC_WEB_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SMAC_WEB_DIR}/.." && pwd)"
WEBUI_DIR="${REPO_ROOT}/app/static/webui"

echo "==> npm run build (smac_web)"
(cd "${SMAC_WEB_DIR}" && npm run build)

echo "==> copying smac_web/dist -> app/static/webui"
rm -rf "${WEBUI_DIR}"
mkdir -p "${WEBUI_DIR}"
cp -r "${SMAC_WEB_DIR}/dist/." "${WEBUI_DIR}/"

echo "==> git diff --exit-code -- app/static/webui"
cd "${REPO_ROOT}"
if ! git diff --exit-code -- app/static/webui; then
  echo
  echo "FRESH-BUILD CHECK FAILED: app/static/webui does not match a fresh" >&2
  echo "build of smac_web/src. Commit the rebuilt bundle (or investigate" >&2
  echo "why the build isn't reproducible) before this gate can pass." >&2
  exit 1
fi

echo "app/static/webui is fresh."

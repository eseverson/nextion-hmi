#!/usr/bin/env bash
# Clones the third-party Nextion tooling into ./tools/ for exploration.
# Idempotent — safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS_DIR="$REPO_ROOT/tools"
mkdir -p "$TOOLS_DIR"

clone_or_pull() {
  local url="$1" dir="$2"
  if [ -d "$TOOLS_DIR/$dir/.git" ]; then
    echo "[setup] updating $dir"
    git -C "$TOOLS_DIR/$dir" pull --ff-only --quiet || true
  else
    echo "[setup] cloning $dir"
    git clone --depth 1 --quiet "$url" "$TOOLS_DIR/$dir"
  fi
}

clone_or_pull https://github.com/UNUF/TFTTool.git           TFTTool
clone_or_pull https://github.com/UNUF/nxt-doc.git           nxt-doc
clone_or_pull https://github.com/MMMZZZZ/Nextion2Text.git   Nextion2Text
clone_or_pull https://github.com/UNUF/nextion-tft-uploader.git nextion-tft-uploader

echo "[setup] done. tools available under $TOOLS_DIR"

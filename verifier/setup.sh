#!/bin/bash
# Setup script for webdev-interactive-verifier
# Run: bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

# 0. Check prerequisites: node >= 22.15, pnpm 9
echo "=== Checking prerequisites ==="
node_ver=$(node --version 2>/dev/null || echo "none")
pnpm_ver=$(pnpm --version 2>/dev/null || echo "none")
echo "  node: $node_ver  (need >= v22.15)"
echo "  pnpm: $pnpm_ver  (need 9.x)"

if [[ "$node_ver" == "none" ]]; then
    echo "ERROR: node not found. Install node >= 22.15 via nvm."
    exit 1
fi
if [[ "$pnpm_ver" == "none" ]]; then
    echo "ERROR: pnpm not found. Install pnpm 9.x: npm install -g pnpm@9"
    exit 1
fi

# 0.5. Install ffmpeg (needed for screencast video encoding)
echo ""
echo "=== Step 0.5: Install ffmpeg ==="
if command -v ffmpeg &>/dev/null; then
    echo "  ffmpeg already installed: $(ffmpeg -version 2>&1 | head -1)"
else
    echo "  Installing ffmpeg..."
    if command -v brew &>/dev/null; then
        brew install ffmpeg
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y ffmpeg
    elif command -v yum &>/dev/null; then
        sudo yum install -y ffmpeg
    else
        echo "  WARNING: Could not install ffmpeg automatically. Please install it manually."
        echo "  Screencast video encoding will be unavailable without ffmpeg."
    fi
fi

# pnpm install flags shared by every pnpm step below.
#   - package-import-method=copy: avoid hardlinking files from pnpm's content store
#     into node_modules. hardlinks caused the cluster pull to fail with
#     EMLINK ("too many links") on an @mui/icons-material file that accumulated
#     65k+ links. copy mode always produces nlink=1. Image is larger but pullable.
#   - registry: uses the standard npm registry by default. Override via
#     NPM_REGISTRY env var if you need an internal/corporate mirror.
PNPM_INSTALL_FLAGS=(
  "--config.package-import-method=copy"
  "--registry" "${NPM_REGISTRY:-https://registry.npmjs.org}"
)

# 1. Install Agent-TARS dependencies
echo ""
echo "=== Step 1: pnpm install in UI-TARS-desktop/multimodal ==="
cd UI-TARS-desktop/multimodal
pnpm install "${PNPM_INSTALL_FLAGS[@]}"
cd ../..

# 1b. Seed the verifier's runtime scaffold template (node_modules baked in so
#     the verifier doesn't have to resolve fresh on every request).
echo ""
echo "=== Step 1b: pnpm install in verifier scaffold template ==="
TEMPLATE_DIR="temp/react_clean/templates/webdev_arena_react_scaffold_template"
if [ -d "$TEMPLATE_DIR" ]; then
    cd "$TEMPLATE_DIR"
    pnpm install "${PNPM_INSTALL_FLAGS[@]}"
    cd - > /dev/null
else
    echo "  skipped: $TEMPLATE_DIR not present"
fi

# 2. Install Python dependencies
echo ""
echo "=== Step 2: pip install requirements ==="
pip install -r requirements.txt --break-system-packages 2>/dev/null \
    || pip install -r requirements.txt 2>/dev/null \
    || pip install -r requirements.txt -i https://pypi.org/simple/ --break-system-packages 2>/dev/null \
    || pip install -r requirements.txt -i https://pypi.org/simple/

# Install Playwright Chromium browser
echo ""
echo "=== Step 2b: Install Playwright Chromium ==="
playwright install chromium

# 3. Rebuild Agent-TARS patches
echo ""
echo "=== Step 3: Rebuild TARS patches ==="
bash rebuild_tars_patch.sh

echo ""
echo "=== Setup complete ==="
echo ""
echo "Start the verifier:"
echo "  export https_proxy= http_proxy= all_proxy="
echo "  python run_verifier_server.py --port 8333 --workers 1"

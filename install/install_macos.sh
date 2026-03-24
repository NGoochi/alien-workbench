#!/bin/bash
# Alien Node Plugin Installer — macOS
# Run from the repo root directory

GH_LIBRARIES="$HOME/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper/Libraries"
ZIP_SOURCE="install/alien-plugin.zip"
TEMP_DIR=$(mktemp -d)

echo "════════════════════════════════════════════"
echo " Alien Node Plugin Installer (macOS)"
echo "════════════════════════════════════════════"

if [ ! -f "$ZIP_SOURCE" ]; then
    echo "[ERROR] Could not find $ZIP_SOURCE"
    echo "        Make sure you're running this from the repo root."
    exit 1
fi

# Try the standard path first, then the GUID-specific path
if [ ! -d "$GH_LIBRARIES" ]; then
    GH_LIBRARIES="$HOME/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-4343-4035-989e-044e8580d9cf)/Libraries"
fi

if [ ! -d "$GH_LIBRARIES" ]; then
    echo "[ERROR] Grasshopper Libraries folder not found."
    echo "        Checked:"
    echo "          ~/Library/.../Grasshopper/Libraries/"
    echo "          ~/Library/.../Grasshopper (b45a29b1-...)/Libraries/"
    echo "        Is Rhino 8 installed? Open Grasshopper once to create the folder."
    exit 1
fi

if pgrep -x "Rhinoceros" > /dev/null 2>&1; then
    echo "[WARNING] Rhino appears to be running."
    echo "          Close Rhino before installing, or the file may be locked."
    read -p "Continue anyway? (y/n): " CONTINUE
    if [ "$CONTINUE" != "y" ] && [ "$CONTINUE" != "Y" ]; then
        exit 0
    fi
fi

echo "Extracting plugin files..."
unzip -o "$ZIP_SOURCE" -d "$TEMP_DIR"

echo "Copying AlienNode.gha to Libraries folder..."
cp "$TEMP_DIR/AlienNode.gha" "$GH_LIBRARIES/AlienNode.gha"

echo "Copying web UI files..."
mkdir -p "$GH_LIBRARIES/web"
cp "$TEMP_DIR/web/dashboard.html" "$GH_LIBRARIES/web/dashboard.html"
cp "$TEMP_DIR/web/node-editor.html" "$GH_LIBRARIES/web/node-editor.html"

if [ -f "$TEMP_DIR/AlienNode.deps.json" ]; then
    cp "$TEMP_DIR/AlienNode.deps.json" "$GH_LIBRARIES/AlienNode.deps.json"
fi
if [ -f "$TEMP_DIR/AlienNode.runtimeconfig.json" ]; then
    cp "$TEMP_DIR/AlienNode.runtimeconfig.json" "$GH_LIBRARIES/AlienNode.runtimeconfig.json"
fi

rm -rf "$TEMP_DIR"

echo ""
echo "[DONE] Plugin installed to: $GH_LIBRARIES"
echo "       Start Rhino + Grasshopper and look for Alien in the Script tab."

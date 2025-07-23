#!/bin/bash

REQUIRED_MODULES=("discord.py" "PyYAML" "google-api-python-client")

echo "🔍 Checking for Python 3 installation..."
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed."
    exit 1
fi

LOCAL_VERSION=$(python3 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))")
echo "📦 Local Python version: $LOCAL_VERSION"

echo "🌐 Fetching latest Python 3 version..."
LATEST_VERSION=$(curl -s https://www.python.org/downloads/ | grep -oP 'Latest Python 3 Release - Python \K3\.[0-9]+\.[0-9]+' | head -1)

if [ -z "$LATEST_VERSION" ]; then
    echo "⚠️  Failed to fetch latest Python version."
else
    echo "🌍 Latest Python 3 version: $LATEST_VERSION"
    if [ "$LOCAL_VERSION" != "$LATEST_VERSION" ]; then
        echo "⬆️  Your Python version is outdated."
    else
        echo "✅ You have the latest Python 3 version."
    fi
fi

echo ""
echo "📦 Checking for required Python packages..."
for MODULE in "${REQUIRED_MODULES[@]}"; do
    python3 -c "import $MODULE" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "✅ $MODULE is installed."
    else
        echo "📥 Installing $MODULE..."
        pip3 install "$MODULE"
    fi
done

echo "✅ All dependencies checked."

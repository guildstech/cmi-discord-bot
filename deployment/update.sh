#!/bin/bash
# CMI Bot Update Script
#
# This script pulls the latest code from GitHub and restarts the bot
#
# Installation:
#   1. Copy to server: cp deployment/update.sh /home/ubuntu/update.sh
#   2. Make executable: chmod +x /home/ubuntu/update.sh
#
# Usage:
#   ./update.sh

echo "=========================================="
echo "CMI Bot Update Script"
echo "=========================================="

BOT_DIR="/home/ubuntu/CMI-disc-bot"

# Navigate to bot directory
cd "$BOT_DIR" || exit 1

# Show current version (commit hash)
echo "Current version:"
git log -1 --oneline

# Pull latest changes from GitHub
echo ""
echo "Pulling latest changes..."
git pull

if [ $? -ne 0 ]; then
    echo "ERROR: Git pull failed. Check for conflicts or network issues."
    exit 1
fi

# Show new version
echo ""
echo "New version:"
git log -1 --oneline

# Update Python dependencies (in case requirements.txt changed)
echo ""
echo "Updating Python dependencies..."
pip3 install -r requirements.txt

# Restart the bot service
echo ""
echo "Restarting bot service..."
sudo systemctl restart cmi-bot

# Wait a moment and check status
sleep 2
sudo systemctl status cmi-bot --no-pager

echo ""
echo "=========================================="
echo "Update complete!"
echo "=========================================="

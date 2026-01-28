#!/bin/bash
# CMI Bot Hourly Backup Script
#
# Installation:
#   1. Copy this script: cp deployment/backup.sh /home/ubuntu/backup.sh
#   2. Make executable: chmod +x /home/ubuntu/backup.sh
#   3. Add to crontab: crontab -e
#   4. Add line: 0 * * * * /home/ubuntu/backup.sh
#      (This runs every hour at minute 0)
#
# Manual backup: ./backup.sh

# Configuration
BOT_DIR="/home/ubuntu/CMI-disc-bot"
DB_FILE="$BOT_DIR/cmi.db"
BACKUP_DIR="$BOT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/cmi_backup_$TIMESTAMP.db"

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Create backup
if [ -f "$DB_FILE" ]; then
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "[$(date)] Backup created: $BACKUP_FILE" >> "$BOT_DIR/backup.log"
    
    # Delete backups older than 7 days
    find "$BACKUP_DIR" -name "cmi_backup_*.db" -mtime +7 -delete
    
    # Optional: Count remaining backups
    BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/cmi_backup_*.db 2>/dev/null | wc -l)
    echo "[$(date)] Total backups: $BACKUP_COUNT" >> "$BOT_DIR/backup.log"
else
    echo "[$(date)] ERROR: Database file not found at $DB_FILE" >> "$BOT_DIR/backup.log"
    exit 1
fi

# Optional: Copy weekly backup to external location (e.g., Dropbox, Google Drive)
# Uncomment and customize if you want off-server backups
# WEEKDAY=$(date +%u)  # 1=Monday, 7=Sunday
# if [ "$WEEKDAY" -eq 7 ]; then
#     cp "$BACKUP_FILE" "/path/to/external/backup/location/"
# fi

#!/bin/bash
# CMI Database Backup Script
# Keeps last 7 days of backups

BACKUP_DIR=~/cmi-backups
DB_PATH=~/cmi-discord-bot/cmi.db
DATE=$(date +%Y-%m-%d)
BACKUP_FILE=$BACKUP_DIR/cmi_backup_$DATE.db

# Create backup
if [ -f $DB_PATH ]; then
    cp $DB_PATH $BACKUP_FILE
    echo "$(date): Backup created: $BACKUP_FILE"
    
    # Delete backups older than 7 days
    find $BACKUP_DIR -name "cmi_backup_*.db" -mtime +7 -delete
    echo "$(date): Old backups cleaned up"
else
    echo "$(date): ERROR - Database file not found at $DB_PATH"
fi

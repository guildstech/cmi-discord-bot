# Production Deployment - Summary of Changes

This document summarizes all the production-ready features added to the CMI Discord bot.

---

## 🎯 Overview

The bot now includes:
1. ✅ Automatic data cleanup (90-day retention)
2. ✅ Auto-restart on crashes (systemd)
3. ✅ Log rotation (prevents disk fill-up)
4. ✅ Hourly database backups
5. ✅ Easy update mechanism (Git-based)
6. ✅ Health monitoring support
7. ✅ Graceful shutdown handling
8. ✅ Broadcast command for owner announcements
9. ✅ Locked dependency versions

---

## 📁 New Files Created

### `/deployment/` directory:
- **`SETUP_INSTRUCTIONS.md`** - Complete deployment guide for Oracle Cloud
- **`cmi-bot.service`** - systemd service file (auto-restart)
- **`logrotate-cmi-bot`** - Log rotation config (keeps 7 days)
- **`backup.sh`** - Hourly backup script
- **`update.sh`** - Easy update script (git pull + restart)
- **`BROADCAST_COMMAND.md`** - Documentation for `/broadcast` command

### Root directory:
- **`requirements.txt`** - Locked Python dependencies

---

## 🔧 Code Changes in `bot.py`

### 1. **Automatic Cleanup Task** (Lines ~828-856)
```python
@tasks.loop(hours=24)
async def cleanup_old_cmi_task():
    # Deletes CMI entries with return date > 90 days old
```
- Runs daily at midnight (or whenever bot starts + 24h)
- Logs deletion count
- Prevents unbounded database growth

### 2. **Owner ID Configuration** (Line ~38)
```python
OWNER_IDS = [None]  # Set your Discord user ID(s) here
```
- Required for broadcast feature
- Replace `None` with your Discord user ID
- Can add multiple IDs: `[123456789012345678, 987654321098765432]`

### 3. **Health Check Server** (Lines ~5161-5194)
```python
class HealthCheckHandler(BaseHTTPRequestHandler):
    # HTTP server on port 8080 for monitoring
```
- Responds to `http://server-ip:8080/health`
- Returns bot status (connected/disconnected)
- Used by UptimeRobot for monitoring

### 4. **Graceful Shutdown** (Lines ~5196-5220)
```python
def signal_handler(sig, frame):
    # Handles SIGTERM and SIGINT gracefully
```
- Stops background tasks cleanly
- Closes database connections properly
- Prevents data corruption on restart

### 5. **Broadcast Command** (Lines ~5127-5230)
class BroadcastModal(discord.ui.Modal):
    # Owner-only button in CMI menu to announce to all servers
```
- Button in main menu (only visible to owners)
- Modal popup to enter message
- Sends to all servers' CMI channels
- Provides detailed success/failure report
- No slash command clutter
- Provides detailed success/failure report

### 6. **Updated Imports** (Lines ~1-21)
```python
import signal, sys, http.server, threading
```
- Added for health check and graceful shutdown

### 7. **Help Text Update** (Line ~2446)
- Added line: "CMIs with a return date older than 90 days will be automatically deleted"

---

## 📊 Storage Impact

### Before these changes:
- CMI entries: **Unlimited growth** ❌
- Log files: **Unlimited growth** ❌
- Backups: **None** ❌

### After these changes:
- CMI entries: **Max ~18MB** (90 days × 1000 entries/day) ✅
- Log files: **Max ~10MB** (7 days compressed) ✅
- Backups: **Max ~170MB** (7 days × 24 hours × ~1MB) ✅
- **Total worst case: ~200MB** for everything

---

## 🚀 Deployment Checklist

### On Your Local Machine (Windows):
- [x] Set `OWNER_IDS = [your_discord_id]` in bot.py (replace `None` with your Discord user ID)
- [ ] Commit and push to GitHub:
  ```bash
  git add .
  git commit -m "Production deployment ready"
  git push
  ```

### On Oracle Cloud VM:
- [ ] Follow `deployment/SETUP_INSTRUCTIONS.md`
- [ ] Set up systemd service
- [ ] Configure log rotation
- [ ] Set up hourly backups (cron)
- [ ] Configure UptimeRobot monitoring
- [ ] Test `/broadcast` command

---

## 🎛️ New Commands

### For Users:
- No changes - all existing commands work the same

### Broadcast button in `/cmi` menu** - Send announcement to all servers
  - Only visible to users in OWNER_IDS list
  - Opens modal popup to type message<text>`** - Send announcement to all servers
  - Only you can use this
  - See `deployment/BROADCAST_COMMAND.md` for details

---

## 📈 Monitoring Setup

### UptimeRobot Configuration:
1. Create account at https://uptimerobot.com (free)
2. Add monitor:
   - Type: HTTP(s)
   - URL: `http://YOUR_SERVER_IP:8080/health`
   - Interval: 5 minutes
3. Add email for alerts
4. Done! You'll be notified if bot goes down

### What Gets Monitored:
- ✅ Bot process running
- ✅ Bot connected to Discord
- ✅ HTTP server responding
- ✅ Server network reachable

---

## 🔄 Update Workflow (After Deployment)

### Making Changes:
1. **Local (Windows):**
   - Edit code in VSCode
   - Test locally: `python bot.py`
   - Commit: `git commit -m "Description"`
   - Push: `git push`

2. **Server (Oracle VM):**
   - SSH: `ssh ubuntu@your-server-ip`
   - Update: `./update.sh`
   - Done!

### Rolling Back:
```bash
cd /home/ubuntu/CMI-disc-bot
git log --oneline           # Find working version
git checkout COMMIT_HASH    # Rollback
sudo systemctl restart cmi-bot
```

---

## 🛠️ Maintenance Tasks

### Daily (Automatic):
- ✅ CMI cleanup runs (deletes 90+ day old entries)
- ✅ Log rotation runs (compresses old logs)

### Hourly (Automatic):
- ✅ Database backup created

### Weekly (Manual - Optional):
- Check backup.log: `tail /home/ubuntu/CMI-disc-bot/backup.log`
- Check disk usage: `df -h`
- Review bot.log for errors: `tail -100 /home/ubuntu/CMI-disc-bot/bot.log`

### Monthly (Manual - Optional):
- Update system: `sudo apt update && sudo apt upgrade`
- Review UptimeRobot uptime stats
- Test restore from backup (good practice)

---

## 🐛 Troubleshooting

### Bot won't start:
```bash
sudo journalctl -u cmi-bot -n 50    # Check logs
sudo systemctl status cmi-bot        # Check status
```

### Health check failing:
```bash
curl http://localhost:8080/health    # Test locally
# Check Oracle Cloud firewall rules
```

### Backups not running:
```bash
crontab -l                           # List cron jobs
./backup.sh                          # Run manually
cat /home/ubuntu/CMI-disc-bot/backup.log  # Check log
```

### Database corrupted:
```bash
ls /home/ubuntu/CMI-disc-bot/backups/      # List backups
cp backups/cmi_backup_LATEST.db cmi.db     # Restore
sudo systemctl restart cmi-bot              # Restart
```

---

## 📝 Configuration Files

### `.service` file (systemd):
- Location: `/etc/systemd/system/cmi-bot.service`
- Edit: `sudo nano /etc/systemd/system/cmi-bot.service`
- Reload: `sudo systemctl daemon-reload`

### Logrotate config:
- Location: `/etc/logrotate.d/cmi-bot`
- Edit: `sudo nano /etc/logrotate.d/cmi-bot`
- Test: `sudo logrotate -d /etc/logrotate.d/cmi-bot`

### Crontab (backups):
- Edit: `crontab -e`
- List: `crontab -l`

---

## 🔐 Security Notes

- ✅ Discord token stored in environment variable (not code)
- ✅ Bot owner ID required for `/broadcast`
- ✅ Backups stored on same server (consider off-site weekly backup)
- ⚠️ Port 8080 open for health checks (only responds to /health endpoint)

---

## 📚 Documentation Files

- **`SETUP_INSTRUCTIONS.md`** - Full deployment guide
- **`BROADCAST_COMMAND.md`** - `/broadcast` command usage
- **`README.md`** - General bot information
- **`deployment/`** - All deployment files

---

## ✅ Testing Checklist

Before deploying to production, test:
- [ ] Bot starts successfully
- [ ] `/cmi` command works
- [ ] Create CMI works
- [ ] Health endpoint responds: `curl http://localhost:8080/health`
- [ ] `/broadcast` command works (only for you)
- [ ] Backup script runs: `./backup.sh`
- [ ] Logs are created: `ls -lh bot.log`
- [ ] Auto-restart works: `sudo systemctl restart cmi-bot && sleep 5 && sudo systemctl status cmi-bot`

---

## 🎉 You're Ready!

Your bot is now production-ready with:
- Automatic maintenance
- Monitoring and alerts
- Easy updates
- Data backups
- Graceful handling of restarts

**Estimated monthly cost on Oracle Cloud Free Tier: $0.00** 🎊

---

## 📞 Quick Command Reference

```bash
# Bot Management
sudo systemctl status cmi-bot       # Check status
sudo systemctl restart cmi-bot      # Restart
sudo systemctl stop cmi-bot         # Stop
sudo systemctl start cmi-bot        # Start

# Logs
tail -f bot.log                     # Live bot logs
sudo journalctl -u cmi-bot -f       # Live systemd logs
tail backup.log                     # Backup logs

# Updates
./update.sh                         # Update bot

# Backups
./backup.sh                         # Manual backup
ls -lh backups/                     # List backups

# Health Check
curl http://localhost:8080/health   # Test health endpoint
```

---

**Good luck with your deployment!** 🚀

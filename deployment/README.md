# CMI Bot - Deployment Files

This folder contains everything needed to deploy the CMI Discord bot to production.

---

## 📁 Files in This Folder

| File | Purpose |
|------|---------|
| `QUICK_START.md` | ⭐ **START HERE** - Step-by-step deployment checklist |
| `SETUP_INSTRUCTIONS.md` | Complete deployment guide with detailed explanations |
| `PRODUCTION_SUMMARY.md` | Overview of all production features and changes |
| `BROADCAST_COMMAND.md` | Documentation for `/broadcast` owner command |
| `cmi-bot.service` | systemd service file (auto-restart on crashes) |
| `logrotate-cmi-bot` | Log rotation config (prevents disk fill-up) |
| `backup.sh` | Hourly backup script for database |
| `update.sh` | Easy update script (git pull + restart) |

---

## 🚀 Getting Started

### First Time Deployment:
1. Read `QUICK_START.md` and follow the checklist
2. Refer to `SETUP_INSTRUCTIONS.md` for detailed steps
3. After deployment, read `PRODUCTION_SUMMARY.md` to understand what's running

### Already Deployed?
- **Update bot:** Run `./update.sh` on server
- **Check status:** `sudo systemctl status cmi-bot`
- **View logs:** `tail -f /home/ubuntu/CMI-disc-bot/bot.log`

---

## 📚 Documentation Guide

### For Different Scenarios:

**I'm deploying for the first time:**
→ Start with `QUICK_START.md`

**I need detailed explanations:**
→ Read `SETUP_INSTRUCTIONS.md`

**I want to understand what changed:**
→ Read `PRODUCTION_SUMMARY.md`

**I want to announce something to all servers:**
→ Read `BROADCAST_COMMAND.md`

**Something broke and I need to fix it:**
→ Check "Troubleshooting" section in `SETUP_INSTRUCTIONS.md`

---

## ✨ Features Included

This production deployment includes:

- ✅ **Auto-restart** on crashes (systemd)
- ✅ **90-day data cleanup** (prevents unbounded storage)
- ✅ **Hourly backups** (7 days retention)
- ✅ **Log rotation** (7 days, compressed)
- ✅ **Health monitoring** (UptimeRobot support)
- ✅ **Graceful shutdown** (no data corruption)
- ✅ **Easy updates** (one command)
- ✅ **Broadcast command** (owner announcements)
- ✅ **Locked dependencies** (no surprise breakage)

---

## 🎯 Quick Command Reference

```bash
# Deployment
sudo systemctl start cmi-bot        # Start bot
sudo systemctl stop cmi-bot         # Stop bot
sudo systemctl restart cmi-bot      # Restart bot
sudo systemctl status cmi-bot       # Check status

# Logs
tail -f /home/ubuntu/CMI-disc-bot/bot.log     # Live bot logs
sudo journalctl -u cmi-bot -f                  # Live systemd logs

# Maintenance
./update.sh                         # Update bot
./backup.sh                         # Manual backup
ls -lh backups/                     # List backups
```

---

## 💰 Hosting Cost

**Oracle Cloud Free Tier: $0/month**

The bot uses:
- ~50-100MB RAM
- ~200MB disk (with all backups and logs)
- Minimal CPU
- No external services needed

Oracle's free tier is more than enough!

---

## 🔒 Security Checklist

- [ ] Bot token stored in environment variable (not in code)
- [ ] `OWNER_ID` set in `bot.py` (for `/broadcast` command)
- [ ] Port 8080 open for health checks only
- [ ] Regular system updates: `sudo apt update && sudo apt upgrade`
- [ ] Backups stored securely
- [ ] Never commit tokens to GitHub

---

## 📞 Support

If you run into issues:

1. **Check bot logs:** `tail -100 /home/ubuntu/CMI-disc-bot/bot.log`
2. **Check system logs:** `sudo journalctl -u cmi-bot -n 50`
3. **Check backup logs:** `cat /home/ubuntu/CMI-disc-bot/backup.log`
4. **Test health check:** `curl http://localhost:8080/health`
5. **Verify bot is running:** `sudo systemctl status cmi-bot`

Common fixes:
- Bot won't start → Check token in environment variable
- Health check fails → Check port 8080 open in firewall
- Backups not running → Check crontab: `crontab -l`

---

## 🎓 Learning Resources

**New to Linux/systemd?**
- systemd basics: `man systemd`
- Service files: `man systemd.service`
- Journal logs: `man journalctl`

**New to Git?**
- Git basics: https://git-scm.com/book/en/v2/Getting-Started-Git-Basics
- GitHub guide: https://guides.github.com/

**New to Oracle Cloud?**
- Free tier info: https://www.oracle.com/cloud/free/

---

## 📝 File Permissions

After copying files to server, verify permissions:

```bash
# Service file (systemd)
sudo chmod 644 /etc/systemd/system/cmi-bot.service

# Logrotate config
sudo chmod 644 /etc/logrotate.d/cmi-bot

# Scripts (must be executable)
chmod +x /home/ubuntu/backup.sh
chmod +x /home/ubuntu/update.sh

# Bot files
chmod 644 /home/ubuntu/CMI-disc-bot/bot.py
chmod 644 /home/ubuntu/CMI-disc-bot/requirements.txt
```

---

## 🔄 Update Workflow

### Making Changes Locally:
1. Edit code in VSCode
2. Test locally: `python bot.py`
3. Commit: `git commit -am "Description"`
4. Push: `git push`

### Applying Updates on Server:
1. SSH: `ssh ubuntu@your-server-ip`
2. Update: `./update.sh`
3. Done!

The update script automatically:
- Pulls latest code from GitHub
- Installs new dependencies
- Restarts the bot
- Shows you the status

---

## 🎉 Success Indicators

Your deployment is successful when:
- ✅ `sudo systemctl status cmi-bot` shows "active (running)"
- ✅ `/cmi` command works in Discord
- ✅ `curl http://localhost:8080/health` returns bot status
- ✅ UptimeRobot shows "Up" status
- ✅ `ls backups/` shows backup files
- ✅ `/broadcast` command works (for you only)

---

## 📊 Monitoring Dashboard

Set up monitoring to track:
- **UptimeRobot:** Bot uptime and downtime alerts
- **systemd journal:** Error logs and crash reports
- **bot.log:** CMI activity and cleanup tasks
- **backup.log:** Backup success/failure history

Check weekly:
```bash
# Uptime
systemctl status cmi-bot

# Recent errors
sudo journalctl -u cmi-bot --since "7 days ago" -p err

# Backup status
tail /home/ubuntu/CMI-disc-bot/backup.log

# Disk usage
df -h
```

---

**Everything you need is in this folder. Good luck! 🚀**

---

## 📌 Quick Links

- [Quick Start Checklist](QUICK_START.md) - ⭐ Start here
- [Detailed Setup Guide](SETUP_INSTRUCTIONS.md) - Step-by-step
- [Production Summary](PRODUCTION_SUMMARY.md) - What's included
- [Broadcast Command](BROADCAST_COMMAND.md) - Owner announcements

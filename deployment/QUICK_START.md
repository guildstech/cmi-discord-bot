# Quick Start Checklist

Use this checklist to track your deployment progress.

---

## ⚙️ Pre-Deployment (Do This First)

### On Your Local Machine:
- [ ] Open `bot.py` in VSCode
- [ ] Find line: `OWNER_IDS = [None]`
- [ ] Replace with your Discord user ID (e.g., `OWNER_IDS = [123456789012345678]`)
  - How to find: Discord Settings → Advanced → Developer Mode ON
  - Right-click your name → Copy ID
  - Can add multiple: `OWNER_IDS = [123456, 789012]`
- [ ] Test bot locally: `python bot.py`
- [ ] Verify `/cmi` works in your test Discord server
- [ ] Commit changes:
  ```bash
  git add .
  git commit -m "Production ready"
  git push
  ```

---

## 🌐 Server Setup

### Oracle Cloud VM Setup:
- [ ] Create Oracle Cloud Free Tier account
- [ ] Create Ubuntu 20.04+ VM instance
- [ ] Note your server IP address: `___.___.___.___`
- [ ] SSH into server: `ssh ubuntu@YOUR_IP`

### Install Prerequisites:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip git
```

### Clone Repository:
```bash
cd /home/ubuntu
git clone https://github.com/YOUR_USERNAME/CMI-disc-bot.git
cd CMI-disc-bot
```

### Install Dependencies:
```bash
pip3 install -r requirements.txt
```

---

## 🔐 Configuration

### Set Environment Variables:
```bash
echo 'export DISCORD_TOKEN="YOUR_DISCORD_BOT_TOKEN"' >> ~/.bashrc
source ~/.bashrc
```

### Test Bot (Optional but Recommended):
```bash
python3 bot.py
```
- Should see: "Logged in as..."
- Press Ctrl+C to stop

---

## 🚀 Deploy Services

### 1. Setup systemd (Auto-Start):
```bash
sudo cp deployment/cmi-bot.service /etc/systemd/system/
sudo nano /etc/systemd/system/cmi-bot.service
```
- [ ] Update `Environment="DISCORD_TOKEN=..."` line with your token
- [ ] Save (Ctrl+X, Y, Enter)

```bash
sudo systemctl daemon-reload
sudo systemctl enable cmi-bot
sudo systemctl start cmi-bot
sudo systemctl status cmi-bot
```
- [ ] Verify: Should see "active (running)" in green

### 2. Setup Log Rotation:
```bash
sudo cp deployment/logrotate-cmi-bot /etc/logrotate.d/cmi-bot
sudo logrotate -d /etc/logrotate.d/cmi-bot
```
- [ ] Verify: No errors in output

### 3. Setup Backups:
```bash
cp deployment/backup.sh /home/ubuntu/backup.sh
chmod +x /home/ubuntu/backup.sh
./backup.sh
```
- [ ] Verify: Check `ls backups/` shows a backup file

```bash
crontab -e
```
- [ ] Add line: `0 * * * * /home/ubuntu/backup.sh`
- [ ] Save and exit

### 4. Setup Update Script:
```bash
cp deployment/update.sh /home/ubuntu/update.sh
chmod +x /home/ubuntu/update.sh
```

---

## 📊 Monitoring

### Setup UptimeRobot:
- [ ] Go to https://uptimerobot.com
- [ ] Create free account
- [ ] Add New Monitor:
  - Monitor Type: **HTTP(s)**
  - Friendly Name: **CMI Discord Bot**
  - URL: `http://YOUR_SERVER_IP:8080/health`
  - Monitoring Interval: **5 minutes**
- [ ] Add alert email
- [ ] Create monitor
- [ ] Test: Visit `http://YOUR_SERVER_IP:8080/health` in browser (should see bot status)

**Note:** If health check doesn't work, you may need to open port 8080 in Oracle Cloud firewall:
- Oracle Cloud Console → Networking → Security Lists → Ingress Rules
- Add rule: Port 8080, Source: 0.0.0.0/0

---

## ✅ Testing

### Test Each Feature:
- [ ] `/cmi` command works
- [ ] Create a CMI
- [ ] Edit a CMI
- [ ] Cancel a CMI
- [ ] Broadcast button appears in menu (only for you)
- [ ] Click broadcast button, send test message
- [ ] Check health: `curl http://localhost:8080/health`
- [ ] Restart bot: `sudo systemctl restart cmi-bot` (should come back online)
- [ ] Check logs: `tail -f /home/ubuntu/CMI-disc-bot/bot.log`
- [ ] Verify backup: `ls -lh /home/ubuntu/CMI-disc-bot/backups/`

---

## 📚 Documentation Reference

Read these files for detailed info:
- [ ] `deployment/SETUP_INSTRUCTIONS.md` - Full deployment guide
- [ ] `deployment/PRODUCTION_SUMMARY.md` - Overview of all changes
- [ ] `deployment/BROADCAST_COMMAND.md` - How to use `/broadcast`

---

## 🎯 Common Commands

Save these for daily use:

```bash
# Bot Management
sudo systemctl status cmi-bot       # Check status
sudo systemctl restart cmi-bot      # Restart
tail -f bot.log                     # View live logs

# Updates
./update.sh                         # Update bot from GitHub

# Backups
./backup.sh                         # Manual backup
ls -lh backups/                     # List backups
```

---

## 🐛 Troubleshooting

### Bot not starting:
```bash
sudo journalctl -u cmi-bot -n 50
```

### Check Python errors:
```bash
tail -100 /home/ubuntu/CMI-disc-bot/bot.log
```

### Database issues:
```bash
# Restore from backup
cp backups/cmi_backup_LATEST.db cmi.db
sudo systemctl restart cmi-bot
```

---

## ✨ You're Done!

Once all boxes are checked:
- ✅ Bot is live 24/7
- ✅ Auto-restarts on crashes
- ✅ Monitored by UptimeRobot
- ✅ Backed up hourly
- ✅ Easy to update
- ✅ Production ready!

**Estimated cost: $0/month on Oracle Cloud Free Tier** 🎉

---

## 📞 Need Help?

Check these logs:
1. Bot logs: `tail -100 bot.log`
2. System logs: `sudo journalctl -u cmi-bot -n 50`
3. Backup logs: `cat backup.log`

---

**Pro Tips:**
- Test everything in a private Discord server first
- Keep the `deployment/` folder - you'll refer to it often
- Check UptimeRobot weekly for uptime stats
- Run `./backup.sh` manually before major updates

**Good luck! 🚀**

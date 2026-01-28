# CMI Bot Deployment Instructions

This guide will help you deploy the CMI bot to Oracle Cloud Free Tier (or any Ubuntu server).

---

## Prerequisites

1. **Oracle Cloud Free Tier VM** (or any Ubuntu 20.04+ server)
2. **GitHub account** with this repository
3. **Discord Bot Token** (from Discord Developer Portal)
4. **Your Discord User ID** (for broadcast command)

---

## Step 1: Initial Server Setup

SSH into your Oracle VM:
```bash
ssh ubuntu@your-server-ip
```

Update system packages:
```bash
sudo apt update && sudo apt upgrade -y
```

Install required software:
```bash
sudo apt install -y python3 python3-pip git
```

---

## Step 2: Clone Repository

Clone your bot repository:
```bash
cd /home/ubuntu
git clone https://github.com/YOUR_USERNAME/CMI-disc-bot.git
cd CMI-disc-bot
```

---

## Step 3: Configure Bot

### Set your Discord User ID (for broadcast command):
Edit `bot.py` and find this line near the top:
```python
OWNER_ID = None  # Replace with your Discord user ID
```

Change it to:
```python
OWNER_ID = 123456789012345678  # Your actual Discord user ID
```

To find your Discord User ID:
1. Enable Developer Mode in Discord (Settings → Advanced → Developer Mode)
2. Right-click your username → Copy ID

### Set Discord Token as environment variable:
```bash
echo 'export DISCORD_TOKEN="your_actual_token_here"' >> ~/.bashrc
source ~/.bashrc
```

---

## Step 4: Install Python Dependencies

```bash
pip3 install -r requirements.txt
```

---

## Step 5: Test Bot Locally (Optional but Recommended)

Test the bot before setting up systemd:
```bash
python3 bot.py
```

If it says "Logged in as...", press Ctrl+C and continue. The bot works!

---

## Step 6: Setup systemd Service (Auto-Start)

Copy service file:
```bash
sudo cp deployment/cmi-bot.service /etc/systemd/system/
```

Edit the service file to add your token:
```bash
sudo nano /etc/systemd/system/cmi-bot.service
```

Find this line:
```
Environment="DISCORD_TOKEN=your_token_here"
```

Replace `your_token_here` with your actual Discord token, then save (Ctrl+X, Y, Enter).

Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable cmi-bot
sudo systemctl start cmi-bot
```

Check status:
```bash
sudo systemctl status cmi-bot
```

You should see "active (running)" in green!

---

## Step 7: Setup Log Rotation

Copy logrotate config:
```bash
sudo cp deployment/logrotate-cmi-bot /etc/logrotate.d/cmi-bot
```

Test it:
```bash
sudo logrotate -d /etc/logrotate.d/cmi-bot
```

---

## Step 8: Setup Hourly Backups

Copy and configure backup script:
```bash
cp deployment/backup.sh /home/ubuntu/backup.sh
chmod +x /home/ubuntu/backup.sh
```

Test the backup script:
```bash
./backup.sh
```

Add to crontab (runs every hour):
```bash
crontab -e
```

Add this line at the end:
```
0 * * * * /home/ubuntu/backup.sh
```

Save and exit.

---

## Step 9: Setup Update Script

Copy and configure update script:
```bash
cp deployment/update.sh /home/ubuntu/update.sh
chmod +x /home/ubuntu/update.sh
```

---

## Step 10: Setup Monitoring (UptimeRobot)

1. Go to https://uptimerobot.com and create free account
2. Click "Add New Monitor"
3. Monitor Type: **HTTP(s)**
4. Friendly Name: **CMI Discord Bot**
5. URL: `http://YOUR_SERVER_IP:8080/health`
6. Monitoring Interval: **5 minutes**
7. Add your email for alerts
8. Click "Create Monitor"

Done! You'll get emails if the bot goes down.

---

## Useful Commands

### View live logs:
```bash
tail -f /home/ubuntu/CMI-disc-bot/bot.log
```

Or using systemd:
```bash
sudo journalctl -u cmi-bot -f
```

### Restart bot:
```bash
sudo systemctl restart cmi-bot
```

### Stop bot:
```bash
sudo systemctl stop cmi-bot
```

### Update bot with latest code:
```bash
./update.sh
```

### Check backup status:
```bash
tail /home/ubuntu/CMI-disc-bot/backup.log
ls -lh /home/ubuntu/CMI-disc-bot/backups/
```

### Restore from backup:
```bash
cp /home/ubuntu/CMI-disc-bot/backups/cmi_backup_TIMESTAMP.db /home/ubuntu/CMI-disc-bot/cmi.db
sudo systemctl restart cmi-bot
```

---

## Troubleshooting

### Bot won't start:
```bash
# Check logs for errors
sudo journalctl -u cmi-bot -n 50

# Check if Python dependencies are installed
pip3 list | grep discord
```

### Health check not working:
```bash
# Test health endpoint locally
curl http://localhost:8080/health

# Check if port 8080 is open in Oracle Cloud firewall
# (You may need to add ingress rule in Oracle Cloud Console)
```

### Database corruption:
```bash
# Restore from latest backup
ls /home/ubuntu/CMI-disc-bot/backups/
cp /home/ubuntu/CMI-disc-bot/backups/cmi_backup_LATEST.db /home/ubuntu/CMI-disc-bot/cmi.db
sudo systemctl restart cmi-bot
```

---

## Security Notes

- Never commit your Discord token to GitHub
- Keep your Oracle VM updated: `sudo apt update && sudo apt upgrade`
- The bot token is stored in environment variable (not in code)
- Only you can use the `/broadcast` command (checked by user ID)

---

## Updating the Bot

When you make code changes on your local machine:

1. **Local (Windows):**
   ```bash
   git add .
   git commit -m "Description of changes"
   git push
   ```

2. **Server (Oracle VM):**
   ```bash
   ./update.sh
   ```

That's it! The update script handles everything.

---

## Rolling Back Changes

If an update breaks something:

```bash
cd /home/ubuntu/CMI-disc-bot
git log --oneline  # Find the commit hash of working version
git checkout COMMIT_HASH
sudo systemctl restart cmi-bot
```

---

## Support

If you run into issues, check:
1. Bot logs: `tail -f /home/ubuntu/CMI-disc-bot/bot.log`
2. System logs: `sudo journalctl -u cmi-bot -n 100`
3. Backup logs: `cat /home/ubuntu/CMI-disc-bot/backup.log`

---

**Your bot is now production-ready!** 🎉

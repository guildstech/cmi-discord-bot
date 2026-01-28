# Pre-Deployment Checklist

Complete this checklist before deploying to production.

---

## ✅ Required Changes (Must Do)

### 1. Set Your Owner ID
- [ ] Open `bot.py`
- [ ] Find line: `OWNER_IDS = [None]`
- [ ] Replace with: `OWNER_IDS = [your_discord_user_id]`
  - Find your ID: Discord Settings → Advanced → Developer Mode → Right-click name → Copy ID
  - Example: `OWNER_IDS = [123456789012345678]`

### 2. Set Discord Token (For Local Testing)
Since the hardcoded token is removed, you must set it as an environment variable:

**Windows (PowerShell):**
```powershell
$env:DISCORD_TOKEN="your_bot_token_here"
python bot.py
```

**Or permanently (add to your PowerShell profile):**
```powershell
[System.Environment]::SetEnvironmentVariable('DISCORD_TOKEN', 'your_token', 'User')
```

---

## 🧪 Local Testing

### Test These Features:
- [ ] Bot starts without errors
- [ ] `/cmi` command appears and works
- [ ] Create a CMI (with dates/times)
- [ ] Edit a CMI
- [ ] Cancel a CMI
- [ ] Manage CMIs view works
- [ ] List CMIs shows entries
- [ ] Set timezone works
- [ ] **Broadcast button appears** (only for you, at bottom of menu)
- [ ] Click broadcast button → modal appears
- [ ] Send test broadcast → receives summary
- [ ] Check logs: `cat bot.log` or `type bot.log`

### Common Issues:

**"DISCORD_TOKEN environment variable not set!"**
- Set token: `$env:DISCORD_TOKEN="your_token"`
- Then run: `python bot.py`

**Broadcast button doesn't appear:**
- Check `OWNER_IDS` has your actual Discord user ID
- Make sure you didn't leave it as `[None]`
- Restart bot after changing

**Bot can't connect:**
- Token might be wrong
- Check Discord Developer Portal for correct token

---

## 🔒 Security Check

Before pushing to GitHub:
- [ ] ✅ Discord token removed from code (done!)
- [ ] No hardcoded tokens anywhere
- [ ] `.gitignore` includes `cmi.db` and `*.log` (should already exist)
- [ ] OWNER_IDS set to real ID (not None)

**NEVER commit:**
- Discord bot tokens
- Database files with real data
- Log files with sensitive info

---

## 📤 Ready to Push to GitHub

Once local testing passes:

```bash
git add .
git commit -m "Production ready: Added 90-day cleanup, backups, monitoring, broadcast feature"
git push
```

---

## 🚀 Deploy to Oracle Cloud

After pushing to GitHub, follow:
1. **[QUICK_START.md](QUICK_START.md)** - Step-by-step checklist
2. **[SETUP_INSTRUCTIONS.md](SETUP_INSTRUCTIONS.md)** - Detailed guide

Key deployment steps:
1. SSH into Oracle VM
2. Clone repository
3. Set `DISCORD_TOKEN` environment variable on server
4. Install dependencies: `pip3 install -r requirements.txt`
5. Setup systemd service (auto-restart)
6. Setup log rotation
7. Setup hourly backups
8. Configure UptimeRobot monitoring

---

## 📊 Post-Deployment Verification

After deploying to Oracle Cloud:

### Check Bot is Running:
```bash
sudo systemctl status cmi-bot
```
Should show: `active (running)`

### Check Health Endpoint:
```bash
curl http://localhost:8080/health
```
Should return bot status

### Check Logs:
```bash
tail -f /home/ubuntu/CMI-disc-bot/bot.log
```
Should show "Logged in as..." and background tasks starting

### Test in Discord:
- [ ] `/cmi` works
- [ ] Create/manage CMIs works
- [ ] Broadcast button appears (for you only)
- [ ] Send test broadcast to verify

### Verify Automated Tasks:
- [ ] Check backup created: `ls -lh /home/ubuntu/CMI-disc-bot/backups/`
- [ ] Check logs rotating: `ls -lh /home/ubuntu/CMI-disc-bot/bot.log*`

---

## 🎉 You're Live!

Once everything checks out:
- ✅ Bot running 24/7 on Oracle Cloud (free!)
- ✅ Auto-restarts on crashes
- ✅ Hourly backups
- ✅ 90-day automatic cleanup
- ✅ Health monitoring ready
- ✅ Broadcast feature working
- ✅ Production ready!

**Estimated cost: $0/month** 🎊

---

## 📞 Quick Reference

**Start bot locally:**
```powershell
$env:DISCORD_TOKEN="your_token"
python bot.py
```

**Check bot status (server):**
```bash
sudo systemctl status cmi-bot
```

**View logs (server):**
```bash
tail -f /home/ubuntu/CMI-disc-bot/bot.log
```

**Update bot (server):**
```bash
./update.sh
```

**Restart bot (server):**
```bash
sudo systemctl restart cmi-bot
```

---

## ⚠️ Important Notes

1. **Token Security:** Never commit your Discord token to GitHub
2. **Test Locally First:** Always test changes locally before deploying
3. **Backup Before Updates:** Backups run hourly, but you can manually run `./backup.sh`
4. **Monitor Health:** Check UptimeRobot weekly for uptime stats
5. **Owner ID:** Can add multiple owners anytime by updating `OWNER_IDS` list

---

**Good luck with your deployment!** 🚀

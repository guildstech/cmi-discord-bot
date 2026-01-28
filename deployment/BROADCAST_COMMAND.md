# Broadcast Feature

The broadcast feature allows bot owners to send announcements to all servers using the bot through an easy-to-use button in the main CMI menu.

---

## Setup

1. **Set Your Discord User ID:**
   
   Edit `bot.py` and find this line (near the top):
   ```python
   OWNER_IDS = [None]  # Example: [123456789012345678]
   ```
   
   Replace `None` with your actual Discord user ID.

2. **Find Your Discord User ID:**
   - Enable Developer Mode: Discord Settings → Advanced → Developer Mode (toggle ON)
   - Right-click your username anywhere in Discord
   - Click "Copy ID"
   - Paste that number in the list

   Example:
   ```python
   OWNER_IDS = [123456789012345678]
   ```

3. **Multiple Owners (Optional):**
   You can add multiple owner IDs:
   ```python
   OWNER_IDS = [123456789012345678, 987654321098765432]
   ```

---

## Usage

### How to Broadcast:

1. Type `/cmi` in any Discord server where the bot exists
2. **You'll see a special button: "📢 Broadcast Message"** (only visible to owners)
3. Click the button
4. A popup appears where you type your message (up to 2000 characters)
5. Click "Submit"
6. Bot sends your message to all servers
7. You receive a detailed summary showing success/failure for each server

### Examples:

**Maintenance Announcement:**
```
The bot will restart in 5 minutes for scheduled maintenance. All CMIs will be preserved.
```

**New Feature:**
```
🎉 New feature: You can now return early from CMIs! Use the "Return early" button on your CMI.
```

**Bug Fix:**
```
⚠️ A bug causing incorrect timezone conversions has been fixed. Please verify your CMI times are correct.
```

---

## How It Works

1. **Only owners see the button** - Non-owners don't see "📢 Broadcast Message" in their menu
2. **Button appears in main menu** - Just like "Leadership Tools", but for owners only
3. **Modal popup** - Click button → type message → submit
4. **Bot sends to all servers** - Posts to each server's CMI channel (or first available channel)
5. **Detailed summary** - Shows which servers received the message and which failed

---

## Message Format

Messages are sent as embeds with:
- 📢 Gold-colored header: "Bot Announcement"
- Your message content
- Timestamp
- Footer showing who sent it

Example embed:
```
┌─────────────────────────────────────┐
│  📢 Bot Announcement                │
│                                      │
│  The bot will restart in 5 minutes  │
│  for scheduled maintenance.          │
│                                      │
│  Broadcast by YourUsername           │
│  Today at 3:45 PM                    │
└─────────────────────────────────────┘
```

---

## Security

- ✅ **Completely invisible to non-owners** - Button doesn't appear if you're not in OWNER_IDS list
- ✅ **No slash command clutter** - Uses menu button instead of global `/broadcast` command
- ✅ **Double-checked** - Even if someone tries to hack it, ownership is verified twice
- ✅ **Clean UI** - Follows same design pattern as Leadership Tools

---

## Where the Button Appears

The "📢 Broadcast Message" button appears:
- In the main CMI menu (type `/cmi`)
- On the same row as "Leadership Tools"
- **Only for users listed in OWNER_IDS**
- In **all servers** where the bot exists (since you're the owner everywhere)

---

## Limitations

- Maximum message length: 2000 characters (Discord limit)
- Cannot broadcast to servers where bot has no message permissions
- Cannot broadcast to servers with no accessible text channels
- Summary shows max 25 servers (longer lists are truncated)

---

## Troubleshooting

### "Button doesn't appear"
- Check `OWNER_IDS` in bot.py - is your ID in the list?
- Make sure you replaced `None` with your actual ID
- Restart the bot after changing bot.py
- Wait 5-10 minutes for Discord to sync

### "Failed to broadcast to ServerName"
- Bot may lack permissions in that server
- Check bot's role permissions
- Check summary for specific error details

### "Only bot owners can broadcast messages"
- Your ID is not in OWNER_IDS list
- Double-check you copied the correct ID
- Make sure there are no typos in the list

---

## Best Practices

1. **Test first:** Create a private test server and test the broadcast feature before using in production
2. **Be clear:** Write concise, informative messages
3. **Use sparingly:** Don't spam servers with unnecessary broadcasts
4. **Announce downtime:** Give users advance notice (e.g., "5 minutes warning")
5. **Acknowledge issues:** If something breaks, let users know you're aware and working on it

---

## Example Scenarios

### Planned Maintenance:
```
🔧 Scheduled maintenance in 10 minutes. Bot will be offline for ~5 minutes. All CMI data is safe.
```

### Emergency Fix:
```
⚠️ Critical bug discovered. Bot will restart immediately to apply fix. Downtime: ~30 seconds.
```

### New Feature Announcement:
```
🎉 New feature: CMIs older than 90 days are now auto-deleted to save server space. Your active CMIs are unaffected!
```

### Incident Report:
```
ℹ️ The bot experienced 2 hours of downtime earlier today. Issue resolved. All CMI data intact. Sorry for the inconvenience!
```

---

## Adding/Removing Owners

### Add an Owner:
```python
# Before
OWNER_IDS = [123456789012345678]

# After (add second owner)
OWNER_IDS = [123456789012345678, 987654321098765432]
```

### Remove an Owner:
```python
# Before
OWNER_IDS = [123456789012345678, 987654321098765432]

# After (remove second owner)
OWNER_IDS = [123456789012345678]
```

**Remember to restart the bot after any changes!**

---

## Logging

Every broadcast is logged:
- Check `bot.log` for broadcast records
- Format: `Broadcast by Username completed: X success, Y failed`
- Errors are logged with details for debugging

---

## Comparison: Old vs New Method

| Feature | Old (Slash Command) | New (Menu Button) |
|---------|---------------------|-------------------|
| Visibility | Everyone sees `/broadcast` | Only owners see button |
| Access | Type `/broadcast` anywhere | `/cmi` → Click button |
| UI Clutter | Shows in all slash commands | Hidden in menu |
| User Confusion | "What's this command?" | Invisible to non-owners |
| Design Consistency | Different pattern | Matches existing menus |

**New method is cleaner and more user-friendly!** ✨

---

**Pro Tip:** The broadcast button uses the same pattern as "Leadership Tools" - familiar to admins and completely invisible to regular users!

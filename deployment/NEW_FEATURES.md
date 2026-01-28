# New Features: CSV Export & Daily CMI Reports

## Overview
Two new features have been added to the CMI bot to enhance leadership capabilities:

1. **CSV Export** - Export all CMI entries to a CSV file for external tracking
2. **Daily CMI Reports** - Automated daily reports showing current and upcoming CMIs

---

## 1. CSV Export Feature

### What It Does
Exports all CMI entries for a guild to a CSV file with comprehensive tracking information.

### How to Use
1. Open `/cmi` menu
2. Click **"Leadership Tools"**
3. Click **"Export CMIs to CSV"**
4. Bot will generate and send a CSV file

### CSV Columns
The export includes:
- **User ID** - Discord user ID
- **Username** - Display name of the user
- **Leave Date/Time** - When the CMI starts
- **Return Date/Time** - When the CMI ends (or "Indefinite")
- **Reason** - Absence reason (or blank if none provided)
- **Status** - Scheduled / Active / Completed
- **Timezone** - Timezone label used for this CMI
- **Created Date** - When the CMI entry was created
- **Days Away** - Total duration in days (or "Indefinite")
- **Created By** - "Self" if user created it, or leadership member's name

### Use Cases
- Track absence patterns across time
- Audit who created CMIs (useful for verifying leadership actions)
- Export to external spreadsheet tools for analysis
- Create reports for management or compliance
- Archive historical absence data

---

## 2. Daily CMI Report Feature

### What It Does
Automatically sends a daily report showing all active and upcoming CMIs for the next 7 days.

### How to Configure
1. Open `/cmi` menu
2. Click **"Leadership Tools"**
3. Click **"Daily CMI Report Settings"**
4. Fill in the modal:
   - **Enabled**: Type "yes" to enable or "no" to disable
   - **Report Hour**: Enter hour in 24-hour format (0-23), e.g., "8" for 8 AM
   - **Channel ID**: (Optional) Enter channel ID, or leave empty to use CMI channel

### Default Settings
- **Enabled**: No (must be turned on)
- **Report Hour**: 8 AM (server timezone)
- **Channel**: CMI channel (if set), otherwise not sent

### Report Contents
- All CMIs currently active
- All CMIs scheduled to start within next 7 days
- User mention, leave date/time, return date/time
- Reason for each absence
- Sorted by leave date (earliest first)

### Use Cases
- Morning briefings for leadership teams
- Quick overview of who's away today
- Upcoming absence planning
- Automatic status updates without manual checks

---

## Database Changes

### New Table: `guild_daily_report_settings`
```sql
CREATE TABLE IF NOT EXISTS guild_daily_report_settings (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    channel_id INTEGER,
    report_hour INTEGER NOT NULL DEFAULT 8
);
```

### Updated Table: `cmi_entries`
New column added: `created_by_user_id INTEGER`
- Tracks who created the CMI (user themselves or leadership)
- Used in CSV export to show "Self" vs leadership name
- Automatically populated for all new CMIs
- Migration code added to handle existing databases

---

## Technical Implementation

### CSV Export
- Function: `generate_csv_export(guild: discord.Guild) -> discord.File`
- Creates CSV in-memory (no file storage)
- Respects server timezone for date formatting
- Status calculation: Scheduled, Active, or Completed
- Filename: `cmi_export_{guild_name}_{YYYYMMDD}.csv`

### Daily Report
- Background task: `daily_report_task()` (runs every hour)
- Checks each guild's settings for enabled reports
- Compares current hour (in server timezone) to configured report hour
- Sends to configured channel or falls back to CMI channel
- Function: `generate_daily_cmi_report(guild_id, server_tz) -> str`
- Shows next 7 days of CMIs (same as "List CMIs" flow)

### Database Helpers
```python
get_daily_report_settings(guild_id) -> (enabled, channel_id, report_hour)
set_daily_report_settings(guild_id, enabled, channel_id, report_hour)
```

---

## Testing Checklist

### CSV Export Testing
- [ ] Export with no CMIs (should work, empty CSV)
- [ ] Export with various CMI states (active, scheduled, completed)
- [ ] Verify "Created By" shows "Self" for user-created CMIs
- [ ] Verify "Created By" shows leadership name for leadership-created CMIs
- [ ] Check Status calculation (Scheduled/Active/Completed)
- [ ] Verify "Indefinite" appears for open-ended CMIs
- [ ] Test with different timezones
- [ ] Confirm CSV downloads as attachment

### Daily Report Testing
- [ ] Enable report and verify settings save
- [ ] Disable report and verify it doesn't send
- [ ] Test with custom channel ID
- [ ] Test with default channel (CMI channel)
- [ ] Verify report sends at configured hour (use different hours for testing)
- [ ] Check report shows current CMIs
- [ ] Check report shows upcoming CMIs (within 7 days)
- [ ] Verify CMIs beyond 7 days are excluded
- [ ] Test with no active/upcoming CMIs (should send "No CMIs" message)
- [ ] Test report formatting and mentions work correctly

### General Testing
- [ ] Verify existing CMI creation still works
- [ ] Verify "Create CMI for Others" tracks creator properly
- [ ] Check Leadership Help shows new sections
- [ ] Verify new buttons appear in Leadership Tools
- [ ] Test with existing database (migration for created_by_user_id works)
- [ ] Confirm no impact on existing features

---

## Documentation Updates

### Leadership Help Embed
Updated to include:
- **📊 Export CMIs to CSV** section with column descriptions
- **📅 Daily CMI Report Settings** section with configuration instructions
- Best practices updated to mention exports and reports

### User-Facing Changes
- Two new buttons in Leadership Tools view
- New modal for daily report configuration
- CSV export generates file download

---

## Troubleshooting

### CSV Export Issues
**Problem**: Export fails or shows errors
- **Check**: Bot has permission to send files in channel
- **Check**: Database has CMI entries (empty export is valid but might look wrong)

**Problem**: "Created By" shows "Unknown"
- **Cause**: User who created CMI has left the server
- **Solution**: This is expected behavior (shows ID in parentheses)

### Daily Report Issues
**Problem**: Report not sending
- **Check**: Report is enabled in settings
- **Check**: Current hour matches report hour (in server timezone)
- **Check**: Bot has permission to send messages in target channel
- **Check**: CMI channel is configured (if using default)

**Problem**: Report sends at wrong time
- **Cause**: Server timezone not set correctly
- **Solution**: Set server timezone in Leadership Tools
- **Note**: Report hour is in server timezone, not UTC

**Problem**: Report shows no CMIs but users are away
- **Check**: CMIs have return dates within 7 days (or are currently active)
- **Check**: Query: `leave_dt <= now + 7 days AND (return_dt IS NULL OR return_dt >= now)`

---

## Migration Notes

### Existing Databases
- `created_by_user_id` column added with ALTER TABLE
- Migration code uses try/except to handle existing columns
- Existing CMIs will have `NULL` for created_by_user_id (CSV shows as "Unknown")
- New CMIs automatically populate the field

### Backwards Compatibility
- All existing features continue to work unchanged
- New features are opt-in (CSV must be requested, reports disabled by default)
- No breaking changes to database schema

---

## Future Enhancements (Ideas)

### CSV Export
- [ ] Filter by date range
- [ ] Export only active/scheduled/completed
- [ ] Include role assignment history
- [ ] Add more statistics (average duration, most common reasons)

### Daily Reports
- [ ] Multiple reports per day
- [ ] Different report formats (summary vs detailed)
- [ ] Mention specific roles in report
- [ ] Weekly summary reports
- [ ] Custom report templates

---

## Version History
- **v1.1** (2024-01-XX) - Added CSV export and daily report features
- **v1.0** - Initial release with core CMI functionality

# ============================================================
# Section 1 — Imports & Global Setup
# ============================================================

import discord
from discord import app_commands
from discord.ext import commands, tasks

from datetime import datetime, date, time, timezone, timedelta
from zoneinfo import ZoneInfo

import asyncio
import sqlite3
import os
import re
from pathlib import Path

# Load environment variables from .env file (if exists)
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip()
import traceback
import logging
import signal
import sys
from difflib import get_close_matches
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

# Basic logging setup writes to console and bot.log for troubleshooting.
# force=True ensures we override any prior logging config from discord.py.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)
logging.info("CMI Bot logging initialized")


# ============================================================
# Bot Token
# ============================================================
# IMPORTANT: Set your Discord bot token as an environment variable
# Never hardcode tokens in your code!
#
# For local testing (Windows):
#   $env:DISCORD_TOKEN="your_token_here"
#   python bot.py
#
# For production (Oracle Cloud), set in systemd service file
TOKEN = os.environ.get("DISCORD_TOKEN")

if not TOKEN:
    logging.error("DISCORD_TOKEN environment variable not set!")
    logging.error("Set it with: $env:DISCORD_TOKEN='your_token' (Windows) or export DISCORD_TOKEN='your_token' (Linux)")
    exit(1)

# ============================================================
# Bot Owner IDs (for broadcast feature)
# ============================================================
# IMPORTANT: Set as environment variable (comma-separated for multiple owners)
# To find your ID: Enable Developer Mode in Discord → Right-click your name → Copy ID
# Example: DISCORD_OWNER_IDS="123456789012345678,987654321098765432"
OWNER_IDS_STR = os.environ.get("DISCORD_OWNER_IDS", "")
OWNER_IDS = [int(id.strip()) for id in OWNER_IDS_STR.split(",") if id.strip()]


# ============================================================
# Discord Intents & Bot Initialization
# ============================================================
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ============================================================
# Database Path
# ============================================================
DB_PATH = "cmi.db"
# ============================================================
# Section 2 — Database Setup
# ============================================================

def get_db_connection():
    """Return a SQLite connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize all required tables if they do not already exist."""
    conn = get_db_connection()
    cur = conn.cursor()

    # Store all CMIs
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS cmi_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            leave_dt TEXT NOT NULL,
            return_dt TEXT,
            reason TEXT,
            timezone_label TEXT,
            created_at TEXT NOT NULL,
            created_by_user_id INTEGER
        );
        """
    )
    
    # Add created_by_user_id column if it doesn't exist (migration for existing databases)
    try:
        cur.execute("ALTER TABLE cmi_entries ADD COLUMN created_by_user_id INTEGER")
        logging.info("Added created_by_user_id column to cmi_entries")
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Store guild settings (server timezone text)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_settings (
            guild_id INTEGER PRIMARY KEY,
            server_timezone TEXT NOT NULL
        );
        """
    )

    # Store per-user timezone settings (per guild)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_timezones (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            timezone TEXT NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        """
    )

    # Store CMI channel restriction (per guild)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_channels (
            guild_id INTEGER PRIMARY KEY,
            cmi_channel_id INTEGER
        );
        """
    )

    # Store away role per guild
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_away_roles (
            guild_id INTEGER PRIMARY KEY,
            role_id INTEGER
        );
        """
    )

    # Store nickname prefix per guild
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_nickname_prefix (
            guild_id INTEGER PRIMARY KEY,
            prefix TEXT NOT NULL
        );
        """
    )

    # Store additional leadership roles
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_bot_perm_roles (
            guild_id INTEGER NOT NULL,
            role_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, role_id)
        );
        """
    )

    # Store additional leadership users
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_bot_perm_users (
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (guild_id, user_id)
        );
        """
    )

    # Store daily CMI report settings
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS guild_daily_report_settings (
            guild_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            channel_id INTEGER,
            report_hour INTEGER NOT NULL DEFAULT 8
        );
        """
    )

    conn.commit()
    conn.close()
# ------------------------------------------------------------
# Interval Overlap Detection
# ------------------------------------------------------------
def intervals_overlap(
    start1: datetime,
    end1: datetime | None,
    start2: datetime,
    end2: datetime | None,
) -> bool:
    """Return True if two datetime intervals overlap."""
    if end1 is None:
        end1 = datetime.max.replace(tzinfo=start1.tzinfo)
    if end2 is None:
        end2 = datetime.max.replace(tzinfo=start2.tzinfo)

    return start1 <= end2 and start2 <= end1


async def has_overlapping_cmi(
    guild_id: int,
    user_id: int,
    new_leave_dt: datetime,
    new_return_dt: datetime | None,
    exclude_id: int | None = None,
):
    """
    Check if a new or edited CMI overlaps with existing ones.
    Only considers active or future CMIs (not ones that have already ended).
    Returns (True, conflict_dict) or (False, None).
    """
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, leave_dt, return_dt, reason
        FROM cmi_entries
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    )
    rows = cur.fetchall()
    conn.close()

    now = datetime.now(ZoneInfo("UTC"))

    for row in rows:
        if exclude_id is not None and row["id"] == exclude_id:
            continue

        try:
            existing_leave = datetime.fromisoformat(row["leave_dt"])
        except Exception:
            continue

        try:
            existing_return = (
                datetime.fromisoformat(row["return_dt"])
                if row["return_dt"]
                else None
            )
        except Exception:
            existing_return = None

        # Skip CMIs that have already ended
        if existing_return and existing_return < now:
            continue

        if intervals_overlap(new_leave_dt, new_return_dt, existing_leave, existing_return):
            return True, {
                "id": row["id"],
                "leave_dt": existing_leave,
                "return_dt": existing_return,
                "reason": row["reason"],
            }

    return False, None


# ------------------------------------------------------------
# Discord Timestamp Helper
# ------------------------------------------------------------
def to_discord_timestamp(dt: datetime | None) -> str | None:
    """Convert a datetime to a Discord <t:unix:f> timestamp."""
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt_utc = dt.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt.astimezone(timezone.utc)

    unix_ts = int(dt_utc.timestamp())
    return f"<t:{unix_ts}:f>"


# ============================================================
# Section 3 — Timezone Utilities
# ============================================================

DEFAULT_SERVER_TZ = "Pacific/Auckland"  # Fallback if not set

# Friendly aliases mapped to IANA names
TIMEZONE_ALIASES = {
    # New Zealand
    "NZT": "Pacific/Auckland",
    "NZDT": "Pacific/Auckland",
    "AUCKLAND": "Pacific/Auckland",

    # Australia
    "AEST": "Australia/Sydney",
    "AEDT": "Australia/Sydney",
    "SYDNEY": "Australia/Sydney",
    "MELBOURNE": "Australia/Melbourne",
    "BRISBANE": "Australia/Brisbane",
    "PERTH": "Australia/Perth",

    # North America
    "EST": "America/New_York",
    "EDT": "America/New_York",
    "PST": "America/Los_Angeles",
    "PDT": "America/Los_Angeles",
    "CST": "America/Chicago",
    "CDT": "America/Chicago",

    # Europe
    "GMT": "Europe/London",
    "BST": "Europe/London",
    "LONDON": "Europe/London",
    "CET": "Europe/Berlin",
    "CEST": "Europe/Berlin",
}


def normalize_timezone_input(tz_str: str | None) -> str | None:
    """
    Accepts IANA names (e.g. 'Pacific/Auckland') or friendly aliases ('NZT', 'Sydney').
    Returns a valid IANA name or None if invalid.
    """
    if not tz_str:
        return None

    tz_clean = tz_str.strip()

    # If it looks like an IANA name, try it directly
    if "/" in tz_clean:
        try:
            ZoneInfo(tz_clean)
            return tz_clean
        except Exception:
            return None

    # Otherwise try alias mapping
    alias_key = tz_clean.upper()
    if alias_key in TIMEZONE_ALIASES:
        iana = TIMEZONE_ALIASES[alias_key]
        try:
            ZoneInfo(iana)
            return iana
        except Exception:
            return None

    return None


def get_server_timezone_text(guild_id: int) -> str:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT server_timezone FROM guild_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = cur.fetchone()
    conn.close()

    return row["server_timezone"] if row else DEFAULT_SERVER_TZ


def set_server_timezone_text(guild_id: int, tz_text: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO guild_settings (guild_id, server_timezone)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET server_timezone = excluded.server_timezone
        """,
        (guild_id, tz_text),
    )
    conn.commit()
    conn.close()


def get_user_timezone(guild_id: int, user_id: int) -> str | None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT timezone FROM user_timezones
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    )
    row = cur.fetchone()
    conn.close()
    return row["timezone"] if row else None


def set_user_timezone(guild_id: int, user_id: int, tz_text: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_timezones (guild_id, user_id, timezone)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET timezone = excluded.timezone
        """,
        (guild_id, user_id, tz_text),
    )
    conn.commit()
    conn.close()


def resolve_effective_timezone(
    guild_id: int,
    user_id: int,
    override_tz: str | None = None,
) -> tuple[str, str]:
    """
    Decide which timezone to use for this CMI:
    - If override_tz provided and valid -> use that (label: override)
    - Else if user_timezone exists -> use that (label: user)
    - Else -> use server timezone (label: server)

    Returns (iana_tz, source_label)
    """
    if override_tz:
        iana = normalize_timezone_input(override_tz)
        if iana:
            return iana, "override"

    user_tz = get_user_timezone(guild_id, user_id)
    if user_tz:
        return user_tz, "user"

    server_tz = get_server_timezone_text(guild_id)
    iana = normalize_timezone_input(server_tz)
    if iana:
        return iana, "server"

    return DEFAULT_SERVER_TZ, "server"


# ------------------------------------------------------------
# CMI Channel / Away Role / Nickname Prefix Helpers
# ------------------------------------------------------------
def get_cmi_channel_id(guild_id: int) -> int | None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT cmi_channel_id FROM guild_channels WHERE guild_id = ?",
        (guild_id,),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["cmi_channel_id"]) if row and row["cmi_channel_id"] else None


def set_cmi_channel_id(guild_id: int, channel_id: int | None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO guild_channels (guild_id, cmi_channel_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET cmi_channel_id = excluded.cmi_channel_id
        """,
        (guild_id, channel_id),
    )
    conn.commit()
    conn.close()


async def enforce_cmi_channel(interaction: discord.Interaction) -> bool:
    """
    Returns True if the command is allowed to continue.
    Returns False if the user is in the wrong channel and an error was sent.
    
    If the configured CMI channel was deleted, finds a fallback channel and notifies leadership.
    """
    if not interaction.guild:
        return True

    allowed_id = get_cmi_channel_id(interaction.guild.id)
    if not allowed_id:
        return True
    
    # Check if configured channel still exists
    allowed_channel = interaction.guild.get_channel(allowed_id)
    if not allowed_channel:
        # CMI channel was deleted, find fallback and notify
        fallback = None
        for ch in interaction.guild.text_channels:
            if ch.permissions_for(interaction.guild.me).send_messages:
                fallback = ch
                break
        
        if fallback:
            # Notify leadership about the fallback
            if await is_leadership(interaction):
                await interaction.followup.send(
                    f"⚠️ **Notice**: The configured CMI channel was deleted. "
                    f"Please reconfigure it with `/cmi leadership`. "
                    f"Commands are temporarily allowed in all channels.",
                    ephemeral=True
                )
        # Allow command to proceed since channel was deleted
        return True

    if interaction.channel and interaction.channel.id == allowed_id:
        return True

    await interaction.followup.send(
        f"❌ Please use this command in <#{allowed_id}>.",
        ephemeral=True,
    )
    return False


# ------------------------------------------------------------
# Away Role Handling
# ------------------------------------------------------------
def get_away_role_id(guild_id: int) -> int | None:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT role_id FROM guild_away_roles WHERE guild_id = ?",
        (guild_id,),
    )
    row = cur.fetchone()
    conn.close()
    return int(row["role_id"]) if row and row["role_id"] else None


def set_away_role_id(guild_id: int, role_id: int | None):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO guild_away_roles (guild_id, role_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET role_id = excluded.role_id
        """,
        (guild_id, role_id),
    )
    conn.commit()
    conn.close()


# ------------------------------------------------------------
# Nickname Prefix Handling
# ------------------------------------------------------------
DEFAULT_NICK_PREFIX = "[CMI]"


def get_nickname_prefix(guild_id: int) -> str:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT prefix FROM guild_nickname_prefix WHERE guild_id = ?",
        (guild_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row["prefix"] if row else DEFAULT_NICK_PREFIX


def set_nickname_prefix(guild_id: int, prefix: str):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO guild_nickname_prefix (guild_id, prefix)
        VALUES (?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET prefix = excluded.prefix
        """,
        (guild_id, prefix),
    )
    conn.commit()
    conn.close()


# ------------------------------------------------------------
# Bot leadership permissions storage
# ------------------------------------------------------------
def get_bot_perm_roles(guild_id: int) -> list[int]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT role_id FROM guild_bot_perm_roles WHERE guild_id = ?",
        (guild_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [int(r["role_id"]) for r in rows]


def add_bot_perm_role(guild_id: int, role_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO guild_bot_perm_roles (guild_id, role_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id, role_id) DO NOTHING
        """,
        (guild_id, role_id),
    )
    conn.commit()
    conn.close()


def remove_bot_perm_role(guild_id: int, role_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM guild_bot_perm_roles WHERE guild_id = ? AND role_id = ?",
        (guild_id, role_id),
    )
    conn.commit()
    conn.close()


def get_bot_perm_users(guild_id: int) -> list[int]:
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id FROM guild_bot_perm_users WHERE guild_id = ?",
        (guild_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return [int(r["user_id"]) for r in rows]


def add_bot_perm_user(guild_id: int, user_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO guild_bot_perm_users (guild_id, user_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id, user_id) DO NOTHING
        """,
        (guild_id, user_id),
    )
    conn.commit()
    conn.close()


def remove_bot_perm_user(guild_id: int, user_id: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM guild_bot_perm_users WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    conn.commit()
    conn.close()


# ------------------------------------------------------------
# Daily Report Settings
# ------------------------------------------------------------
def get_daily_report_settings(guild_id: int) -> tuple[bool, int | None, int]:
    """Returns (enabled, channel_id, report_hour)"""
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT enabled, channel_id, report_hour FROM guild_daily_report_settings WHERE guild_id = ?",
        (guild_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row:
        return (bool(row["enabled"]), row["channel_id"], row["report_hour"])
    return (False, None, 8)


def set_daily_report_settings(guild_id: int, enabled: bool, channel_id: int | None, report_hour: int):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO guild_daily_report_settings (guild_id, enabled, channel_id, report_hour)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET
            enabled = excluded.enabled,
            channel_id = excluded.channel_id,
            report_hour = excluded.report_hour
        """,
        (guild_id, 1 if enabled else 0, channel_id, report_hour),
    )
    conn.commit()
    conn.close()


# ------------------------------------------------------------
# Away Role Recompute (single user)
# ------------------------------------------------------------
async def recompute_away_role_for_user(guild: discord.Guild, user_id: int):
    """
    Ensures the away role and nickname prefix are correct for a single user.
    """
    away_role_id = get_away_role_id(guild.id)
    if not away_role_id:
        return

    role = guild.get_role(away_role_id)
    if not role:
        return

    member = guild.get_member(user_id)
    if not member:
        return

    # Use server timezone
    server_tz_name = get_server_timezone_text(guild.id)
    server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
    server_tz = ZoneInfo(server_tz_iana)
    now = datetime.now(server_tz)

    # Fetch CMIs
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT leave_dt, return_dt
        FROM cmi_entries
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild.id, user_id),
    )
    rows = cur.fetchall()
    conn.close()

    is_away = False
    for row in rows:
        try:
            leave_dt = datetime.fromisoformat(row["leave_dt"])
        except Exception:
            continue

        try:
            return_dt = (
                datetime.fromisoformat(row["return_dt"]) if row["return_dt"] else None
            )
        except Exception:
            return_dt = None

        leave_local = leave_dt.astimezone(server_tz)
        return_local = return_dt.astimezone(server_tz) if return_dt else None

        if leave_local <= now and (return_local is None or return_local >= now):
            is_away = True
            break

    prefix = get_nickname_prefix(guild.id)

    # Apply role & nickname
    if is_away:
        if role not in member.roles:
            try:
                await member.add_roles(role, reason="CMI: user currently away")
                logging.info(f"Added away role to {member.display_name} ({member.id}) in {guild.name}")
            except Exception as e:
                logging.error(f"Failed to add away role to {member.display_name} ({member.id}): {e}")

        # Add prefix to nickname
        current = member.nick or member.name
        
        # Only strip existing prefix if member already has the away role
        # (this means WE added it, not another bot)
        if role in member.roles:
            while current.startswith(prefix):
                current = current[len(prefix):].lstrip()
        
        # Now add our prefix
        new_nick = f"{prefix} {current}"
        if len(new_nick) <= 32:
            try:
                await member.edit(nick=new_nick, reason="CMI: applying prefix")
                logging.info(f"Added prefix to {member.display_name} ({member.id}): {new_nick}")
            except Exception as e:
                logging.error(f"Failed to add prefix to {member.display_name} ({member.id}): {e}")

    else:
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="CMI: user no longer away")
            except Exception:
                pass

        current = member.nick
        if current and current.startswith(prefix):
            new_nick = current[len(prefix):].lstrip()
            try:
                await member.edit(nick=new_nick, reason="CMI: removing prefix")
            except Exception:
                pass


# ------------------------------------------------------------
# Periodic Sync Task
# ------------------------------------------------------------
@tasks.loop(minutes=5)
async def away_role_sync_task():
    """
    Periodically sync away roles for all guilds.
    Ensures correctness across restarts.
    """
    await bot.wait_until_ready()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT guild_id, role_id FROM guild_away_roles")
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        guild_id = row["guild_id"]
        role_id = row["role_id"]

        guild = bot.get_guild(guild_id)
        if not guild or not role_id:
            continue

        role = guild.get_role(role_id)
        if not role:
            continue

        # Server timezone
        server_tz_name = get_server_timezone_text(guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        now = datetime.now(server_tz)

        # Fetch all CMIs
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT user_id, leave_dt, return_dt
            FROM cmi_entries
            WHERE guild_id = ?
            """,
            (guild_id,),
        )
        user_rows = cur.fetchall()
        conn.close()

        should_have_role = set()

        for urow in user_rows:
            uid = urow["user_id"]

            try:
                leave_dt = datetime.fromisoformat(urow["leave_dt"])
            except Exception:
                continue

            try:
                return_dt = (
                    datetime.fromisoformat(urow["return_dt"]) if urow["return_dt"] else None
                )
            except Exception:
                return_dt = None

            leave_local = leave_dt.astimezone(server_tz)
            return_local = return_dt.astimezone(server_tz) if return_dt else None

            if leave_local <= now and (return_local is None or return_local >= now):
                should_have_role.add(uid)

        # Get all members who currently have the role
        members_with_role = [m for m in guild.members if role in m.roles]
        
        # Build set of user IDs from CMI list and members with role
        all_relevant_user_ids = should_have_role | {m.id for m in members_with_role}
        
        prefix = get_nickname_prefix(guild.id)
        
        # Only check members who are in CMI list or currently have the role
        for user_id in all_relevant_user_ids:
            member = guild.get_member(user_id)
            if not member:
                continue
            
            has = role in member.roles
            should = user_id in should_have_role

            if should and not has:
                try:
                    await member.add_roles(role, reason="CMI: user currently away")
                except Exception:
                    pass

                # Add prefix to nickname
                current = member.nick or member.name
                
                # Only strip existing prefix if member already has the away role
                # (this means WE added it, not another bot)
                if has:
                    while current.startswith(prefix):
                        current = current[len(prefix):].lstrip()
                
                # Now add our prefix
                new_nick = f"{prefix} {current}"
                if len(new_nick) <= 32:
                    try:
                        await member.edit(nick=new_nick, reason="CMI: applying prefix")
                    except Exception:
                        pass

            elif has and not should:
                try:
                    await member.remove_roles(role, reason="CMI: user no longer away")
                except Exception:
                    pass

                current = member.nick
                if current and current.startswith(prefix):
                    new_nick = current[len(prefix):].lstrip()
                    try:
                        await member.edit(nick=new_nick, reason="CMI: removing prefix")
                    except Exception:
                        pass


@tasks.loop(hours=24)
async def cleanup_old_cmi_task():
    """
    Periodically delete CMI entries where the return date is older than 90 days.
    Runs daily to keep database storage under control.
    """
    await bot.wait_until_ready()

    # Calculate cutoff date: 90 days ago from now
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=90)
    cutoff_iso = cutoff_date.isoformat()

    conn = get_db_connection()
    cur = conn.cursor()
    
    # Delete CMI entries where return_dt exists and is older than 90 days
    cur.execute(
        """
        DELETE FROM cmi_entries
        WHERE return_dt IS NOT NULL
        AND return_dt < ?
        """,
        (cutoff_iso,)
    )
    
    deleted_count = cur.rowcount
    conn.commit()
    conn.close()

    if deleted_count > 0:
        logging.info(f"Cleanup task: Deleted {deleted_count} old CMI entries (return date > 90 days ago)")


@tasks.loop(hours=1)
async def daily_report_task():
    """
    Check every hour if any guild needs their daily CMI report sent.
    Runs at the configured hour in the server's timezone.
    """
    await bot.wait_until_ready()

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT guild_id, channel_id, report_hour
        FROM guild_daily_report_settings
        WHERE enabled = 1
        """
    )
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        guild_id = row["guild_id"]
        channel_id = row["channel_id"]
        report_hour = row["report_hour"]

        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        # Get server timezone
        server_tz_name = get_server_timezone_text(guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        
        # Check if current hour matches report hour
        now = datetime.now(server_tz)
        if now.hour != report_hour:
            continue

        # Determine target channel with fallback logic
        if channel_id:
            channel = guild.get_channel(channel_id)
            if not channel:
                # Daily report channel was deleted, fallback to CMI channel
                channel_id_from_settings = get_cmi_channel_id(guild_id)
                channel = guild.get_channel(channel_id_from_settings) if channel_id_from_settings else None
        else:
            channel_id_from_settings = get_cmi_channel_id(guild_id)
            channel = guild.get_channel(channel_id_from_settings) if channel_id_from_settings else None
        
        # If still no channel, try to find first accessible text channel
        if not channel:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    channel = ch
                    # Notify leadership about fallback
                    try:
                        await ch.send(
                            "⚠️ **Leadership Notice**: The configured CMI/daily report channel was deleted. "
                            f"Using {ch.mention} as fallback. Please reconfigure channels with `/cmi leadership`."
                        )
                    except Exception:
                        pass
                    break
        
        if not channel:
            logging.warning(f"No accessible channel found for daily report in {guild.name}")
            continue

        # Generate and send report
        try:
            report_content = await generate_daily_cmi_report(guild, server_tz)
            if report_content:
                await channel.send(report_content)
                logging.info(f"Sent daily CMI report to {guild.name} (#{channel.name})")
        except Exception as e:
            logging.error(f"Failed to send daily CMI report to {guild.name}: {e}")


async def generate_daily_cmi_report(guild: discord.Guild, server_tz: ZoneInfo) -> str:
    """
    Generate a daily CMI report showing current and upcoming CMIs for the next 7 days.
    Returns a formatted string message.
    """
    now = datetime.now(server_tz)
    end_date = now + timedelta(days=7)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, leave_dt, return_dt, reason, timezone_label, created_at
        FROM cmi_entries
        WHERE guild_id = ?
        AND leave_dt <= ?
        AND (return_dt IS NULL OR return_dt >= ?)
        ORDER BY leave_dt ASC
        """,
        (guild.id, end_date.isoformat(), now.isoformat()),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return "📊 **Daily CMI Report**\n\nNo active or upcoming CMIs for the next 7 days."

    lines = ["📊 **Daily CMI Report**"]
    lines.append(f"Showing CMIs active or starting within the next 7 days.\n")

    for row in rows:
        user_id = row["user_id"]
        leave_dt = datetime.fromisoformat(row["leave_dt"])
        return_dt = datetime.fromisoformat(row["return_dt"]) if row["return_dt"] else None
        reason = row["reason"] or "No reason provided"

        leave_local = leave_dt.astimezone(server_tz)
        return_local = return_dt.astimezone(server_tz) if return_dt else None

        # Format dates
        leave_str = leave_local.strftime("%d/%m/%Y %H:%M")
        return_str = return_local.strftime("%d/%m/%Y %H:%M") if return_local else "Until further notice"

        # Get member info without tagging
        member = guild.get_member(user_id)
        if member:
            # Show nickname if set, otherwise username
            display_name = member.display_name
            username = f"@{member.name}"
            user_display = f"{display_name} ({username})"
        else:
            # User left the server
            user_display = f"User ID: {user_id}"

        lines.append(f"• {user_display}: {leave_str} → {return_str}")
        lines.append(f"  *Reason:* {reason}")

    return "\n".join(lines)


async def generate_csv_export(guild: discord.Guild) -> discord.File:
    """
    Generate a CSV export of all CMI entries for a guild.
    Returns a discord.File ready to send as an attachment.
    """
    import csv
    import io

    server_tz_name = get_server_timezone_text(guild.id)
    server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
    server_tz = ZoneInfo(server_tz_iana)
    now = datetime.now(server_tz)

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, user_id, leave_dt, return_dt, reason, timezone_label, created_at, created_by_user_id
        FROM cmi_entries
        WHERE guild_id = ?
        ORDER BY leave_dt DESC
        """,
        (guild.id,),
    )
    rows = cur.fetchall()
    conn.close()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "User ID",
        "Username",
        "Leave Date/Time",
        "Return Date/Time",
        "Reason",
        "Status",
        "Timezone",
        "Created Date",
        "Days Away",
        "Created By"
    ])

    # Write data rows
    for row in rows:
        user_id = row["user_id"]
        member = guild.get_member(user_id)
        username = f"{member.name}" if member else f"Unknown User ({user_id})"

        leave_dt = datetime.fromisoformat(row["leave_dt"])
        return_dt = datetime.fromisoformat(row["return_dt"]) if row["return_dt"] else None
        reason = row["reason"] or ""
        
        leave_local = leave_dt.astimezone(server_tz)
        return_local = return_dt.astimezone(server_tz) if return_dt else None

        leave_str = leave_local.strftime("%d/%m/%Y %H:%M")
        return_str = return_local.strftime("%d/%m/%Y %H:%M") if return_local else "Indefinite"

        # Determine status
        if leave_local > now:
            status = "Scheduled"
        elif return_local and return_local < now:
            status = "Completed"
        else:
            status = "Active"

        # Calculate days away
        if return_dt:
            days_away = (return_dt - leave_dt).days
        else:
            days_away = "Indefinite"

        # Created date
        created_at = datetime.fromisoformat(row["created_at"])
        created_str = created_at.strftime("%d/%m/%Y %H:%M")

        # Created by (handles NULL for old CMIs)
        created_by_id = row["created_by_user_id"]
        if created_by_id is None:
            created_by = "Unknown (created before tracking)"
        elif created_by_id == user_id:
            created_by = "Self"
        else:
            creator_member = guild.get_member(created_by_id)
            created_by = f"{creator_member.name}" if creator_member else f"Unknown ({created_by_id})"

        writer.writerow([
            f"'{user_id}",  # Prepend ' to force Excel to treat as text
            username,
            leave_str,
            return_str,
            reason,
            status,
            row["timezone_label"],
            created_str,
            days_away,
            created_by
        ])

    # Create file
    output.seek(0)
    file = discord.File(
        io.BytesIO(output.getvalue().encode('utf-8')),
        filename=f"cmi_export_{guild.name}_{now.strftime('%Y%m%d')}.csv"
    )
    output.close()

    return file


# ------------------------------------------------------------
# Date Parsing
# ------------------------------------------------------------
def parse_date(date_str: str | None, tz_info: ZoneInfo | None = None):
    """Parse flexible date formats into a date object.
    
    Args:
        date_str: The date string to parse
        tz_info: The timezone to use for 'today' and 'tomorrow' calculations
    """
    if not date_str:
        return None

    date_str = date_str.strip()
    
    # Handle "Today" and "Tomorrow" (case-insensitive)
    date_str_lower = date_str.lower()
    if date_str_lower == "today":
        # Use timezone-aware now if timezone provided, otherwise use naive
        now = datetime.now(tz_info) if tz_info else datetime.now()
        result = now.date()
        return result
    elif date_str_lower == "tomorrow":
        # Use timezone-aware now if timezone provided, otherwise use naive
        now = datetime.now(tz_info) if tz_info else datetime.now()
        result = (now + timedelta(days=1)).date()
        return result
    
    # Title case for month names
    date_str = date_str.title()

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d %b",
        "%b %d",
        "%d %B",
        "%B %d",
        "%d %b %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%B %d %Y",
        "%d %b %y",  # Added: 2-digit year support (e.g., "1 Jan 26")
        "%b %d %y",
        "%d %B %y",
        "%B %d %y",
        "%d/%m/%y",  # Added: 2-digit year support (e.g., "01/01/26")
        "%d-%m-%y",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(date_str, fmt)

            # Handle missing year (default 1900)
            if parsed.year == 1900:
                today = datetime.now().date()
                parsed = parsed.replace(year=today.year)
                if parsed.date() < today:
                    parsed = parsed.replace(year=today.year + 1)

            result = parsed.date()
            return result
        except ValueError:
            continue

    return None

    return None


# ------------------------------------------------------------
# Time Parsing
# ------------------------------------------------------------
def parse_time(time_str: str | None):
    """Parse flexible time formats into a time object."""
    if not time_str:
        return None

    time_str = time_str.strip().lower()

    # Normalize "9am" → "9 am"
    if time_str.endswith("am") or time_str.endswith("pm"):
        time_str = time_str[:-2] + " " + time_str[-2:]

    formats = [
        "%H:%M",
        "%H",
        "%I:%M %p",
        "%I %p",
    ]

    for fmt in formats:
        try:
            parsed = datetime.strptime(time_str, fmt)
            return parsed.time()
        except ValueError:
            continue

    return None


# ------------------------------------------------------------
# Leadership Permissions
# ------------------------------------------------------------
async def is_leadership(interaction: discord.Interaction) -> bool:
    """Leadership = Administrator or Manage Server."""
    if not interaction.guild:
        return False

    perms = interaction.user.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True

    # Check custom leadership roles/users
    guild_id = interaction.guild.id
    role_ids = set(get_bot_perm_roles(guild_id))
    user_ids = set(get_bot_perm_users(guild_id))

    if role_ids and any(r.id in role_ids for r in interaction.user.roles):
        return True

    if interaction.user.id in user_ids:
        return True

    return False


def is_owner(user_id: int) -> bool:
    """Check if user is a bot owner."""
    return user_id in OWNER_IDS and user_id is not None


# ------------------------------------------------------------
# Timezone Autocomplete Helper
# ------------------------------------------------------------
COMMON_TZ_IANA = [
    "Pacific/Auckland",
    "Australia/Sydney",
    "Australia/Melbourne",
    "Australia/Brisbane",
    "Australia/Perth",
    "America/New_York",
    "America/Chicago",
    "America/Los_Angeles",
    "Europe/London",
    "Europe/Berlin",
]


async def timezone_autocomplete(
    interaction: discord.Interaction,
    current: str,
):
    """Autocomplete for timezone inputs."""
    candidates = set(COMMON_TZ_IANA)
    candidates.update(TIMEZONE_ALIASES.keys())

    current_lower = current.lower()
    results = []

    for label in candidates:
        if current_lower in label.lower():
            if label in TIMEZONE_ALIASES:
                value = TIMEZONE_ALIASES[label]
                display = f"{label} ({value})"
            else:
                value = label
                display = label

            results.append(app_commands.Choice(name=display, value=value))

        if len(results) >= 25:
            break

    if not results and current:
        results.append(app_commands.Choice(name=current, value=current))

    return results
# ============================================================
# Section 6 — User Resolver & Dropdown Components
# ============================================================

# ------------------------------------------------------------
# Advanced User Resolver
# ------------------------------------------------------------
def resolve_users_advanced(guild: discord.Guild, query: str):
    """
    Resolve users by:
    - ID
    - Mention
    - Exact username
    - Exact nickname
    - username#discriminator
    - Case-insensitive exact match
    - Partial match (username or nickname)
    """
    query = query.strip()
    query_lower = query.lower()

    exact_matches = []
    partial_matches = []

    # Normalize @username (not mention)
    if query.startswith("@") and not query.startswith("<@"):
        query = query[1:]
        query_lower = query.lower()

    # 1. Direct ID
    if query.isdigit():
        user = guild.get_member(int(query))
        if user:
            exact_matches.append(user)
            return exact_matches, partial_matches

    # 2. Mention <@ID>
    if query.startswith("<@") and query.endswith(">"):
        inner = query.replace("<@", "").replace(">", "").replace("!", "")
        if inner.isdigit():
            user = guild.get_member(int(inner))
            if user:
                exact_matches.append(user)
                return exact_matches, partial_matches

    # 3. Exact username / nickname / username#discriminator
    for member in guild.members:
        if (
            member.name == query
            or (member.nick and member.nick == query)
            or f"{member.name}#{member.discriminator}" == query
        ):
            exact_matches.append(member)

    if exact_matches:
        return exact_matches, partial_matches

    # 4. Case-insensitive exact match
    for member in guild.members:
        if (
            query_lower == member.name.lower()
            or (member.nick and query_lower == member.nick.lower())
        ):
            exact_matches.append(member)

    if exact_matches:
        return exact_matches, partial_matches

    # 5. Partial matches
    for member in guild.members:
        if (
            query_lower in member.name.lower()
            or (member.nick and query_lower in member.nick.lower())
        ):
            partial_matches.append(member)

    return exact_matches, partial_matches


# ------------------------------------------------------------
# Dropdown for Create CMI
# ------------------------------------------------------------
class UserSelectDropdown(discord.ui.Select):
    def __init__(self, matches):
        options = [
            discord.SelectOption(
                label=f"{m.name}#{m.discriminator}",
                description=(m.nick or "No nickname"),
                value=str(m.id),
            )
            for m in matches
        ]

        super().__init__(
            placeholder="Select a user",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = int(self.values[0])
        target_user = interaction.guild.get_member(user_id)

        modal = CreateCMIModal(target_user=target_user)
        await interaction.response.send_modal(modal)


class UserSelectDropdownView(discord.ui.View):
    def __init__(self, matches):
        super().__init__(timeout=60)
        self.add_item(UserSelectDropdown(matches))


# ------------------------------------------------------------
# Dropdown for Manage CMI
# ------------------------------------------------------------
class UserSelectDropdownForManage(discord.ui.Select):
    def __init__(self, matches):
        options = [
            discord.SelectOption(
                label=f"{m.name}#{m.discriminator}",
                description=(m.nick or "No nickname"),
                value=str(m.id),
            )
            for m in matches
        ]

        super().__init__(
            placeholder="Select a user",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = int(self.values[0])
        target_user = interaction.guild.get_member(user_id)

        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        await cog.show_manage_cmi_ui(interaction, target_member=target_user)


class UserSelectDropdownViewForManage(discord.ui.View):
    def __init__(self, matches):
        super().__init__(timeout=60)
        self.add_item(UserSelectDropdownForManage(matches))


# ============================================================
# Section 7 — Modals (Create CMI, Manage CMI, Timezones, Roles, Channels, Prefix)
# ============================================================

# ------------------------------------------------------------
# Modal: Create CMI (for self or others)
# ------------------------------------------------------------
class CreateCMIModal(discord.ui.Modal):
    def __init__(self, target_user=None):
        # Show who this CMI is for (Discord modal title limit is 45 chars)
        title_suffix = ""
        if target_user:
            name = target_user.display_name or target_user.name
            title_suffix = f" — {name}"

        super().__init__(title=("Create CMI" + title_suffix)[:45])
        self.target_user = target_user

        self.leave_date = discord.ui.TextInput(
            label="Leave Date",
            placeholder="e.g. 29/12/2025 or 29 Dec",
            required=False,
        )
        self.leave_time = discord.ui.TextInput(
            label="Leave Time",
            placeholder="e.g. 14:30 or 2:30pm",
            required=False,
        )
        self.return_date = discord.ui.TextInput(
            label="Return Date",
            placeholder="e.g. 2/1/2026 or 2 Jan",
            required=False,
        )
        self.return_time = discord.ui.TextInput(
            label="Return Time",
            placeholder="e.g. 10:00 or 10am",
            required=False,
        )
        self.reason = discord.ui.TextInput(
            label="Reason (will be publicly viewable)",
            placeholder="Optional reason for your absence",
            required=False,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.leave_date)
        self.add_item(self.leave_time)
        self.add_item(self.return_date)
        self.add_item(self.return_time)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        try:
            await cog.handle_create_from_modal(interaction, self)
        except Exception:
            logging.exception("Error handling CreateCMIModal submission")
            tb = traceback.format_exc()
            try:
                await interaction.response.send_message(
                    "❌ Something went wrong while creating the CMI.",
                    ephemeral=True,
                )
            except Exception:
                pass
            try:
                if interaction.followup:
                    await interaction.followup.send(f"```{tb[:1800]}```", ephemeral=True)
            except Exception:
                pass


# ------------------------------------------------------------
# Modal: Select user for "Create CMI for Others"
# ------------------------------------------------------------
class SelectUserForCMIModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Create CMI for Others")

        self.user_field = discord.ui.TextInput(
            label="User",
            placeholder="ID, @mention, username, nickname, or partial match",
            required=True,
        )
        self.add_item(self.user_field)

    async def on_submit(self, interaction: discord.Interaction):
        query = self.user_field.value.strip()
        guild = interaction.guild

        exact_matches, partial_matches = resolve_users_advanced(guild, query)

        # No matches
        if not exact_matches and not partial_matches:
            return await interaction.response.send_message(
                "❌ No matches — try a different name or ID.",
                ephemeral=True,
            )

        # Exact match → open Create CMI modal (via button to avoid modal-in-modal)
        if exact_matches:
            target_user = exact_matches[0]

            class _TempButton(discord.ui.View):
                def __init__(self, target):
                    super().__init__(timeout=10)
                    self.target = target

                    button = discord.ui.Button(
                        label="Open CMI Form",
                        style=discord.ButtonStyle.primary,
                    )
                    button.callback = self.open_modal
                    self.add_item(button)

                async def open_modal(self, button_interaction: discord.Interaction):
                    modal = CreateCMIModal(target_user=self.target)
                    await button_interaction.response.send_modal(modal)

            return await interaction.response.send_message(
                "Opening CMI creation…",
                view=_TempButton(target_user),
                ephemeral=True,
            )

        # Partial matches → dropdown
        if 1 <= len(partial_matches) <= 25:
            view = UserSelectDropdownView(partial_matches)
            return await interaction.response.send_message(
                "Multiple users match your search. Please select one:",
                view=view,
                ephemeral=True,
            )

        # Too many matches
        return await interaction.response.send_message(
            f"❌ Too many matches ({len(partial_matches)}). Please be more specific.",
            ephemeral=True,
        )


# ------------------------------------------------------------
# Modal: Select user for "Manage CMIs for Others"
# ------------------------------------------------------------
class SelectUserForManageCMIModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Manage CMIs for Another Member")

        self.user_field = discord.ui.TextInput(
            label="User",
            placeholder="ID, @mention, username, nickname, or partial match",
            required=True,
        )
        self.add_item(self.user_field)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        query = self.user_field.value.strip()
        guild = interaction.guild

        exact_matches, partial_matches = resolve_users_advanced(guild, query)

        # No matches
        if not exact_matches and not partial_matches:
            return await interaction.followup.send(
                "❌ No matches — try a different name or ID.",
                ephemeral=True,
            )

        # Exact match → open Manage UI
        if exact_matches:
            target_user = exact_matches[0]
            cog: "CMI" = interaction.client.get_cog("CMI")
            if not cog:
                return await interaction.followup.send(
                    "❌ CMI system is not available.",
                    ephemeral=True,
                )
            return await cog.show_manage_cmi_ui(interaction, target_member=target_user)

        # Partial matches → dropdown
        if 1 <= len(partial_matches) <= 25:
            view = UserSelectDropdownViewForManage(partial_matches)
            return await interaction.followup.send(
                "Multiple users match your search. Please select one:",
                view=view,
                ephemeral=True,
            )

        # Too many matches
        return await interaction.followup.send(
            f"❌ Too many matches ({len(partial_matches)}). Please be more specific.",
            ephemeral=True,
        )


# ------------------------------------------------------------
# Modal: Set My Timezone
# ------------------------------------------------------------
class SetUserTimezoneModal(discord.ui.Modal, title="Set My Timezone"):
    timezone = discord.ui.TextInput(
        label="Your Timezone",
        placeholder="e.g. Pacific/Auckland, Australia/Sydney, NZT, AEST",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        tz_text = self.timezone.value.strip()
        iana = normalize_timezone_input(tz_text)

        if not iana:
            return await interaction.response.send_message(
                f"❌ **Invalid timezone**: `{tz_text}`\n\n"
                "Please use a valid timezone format:\n"
                "• IANA format: `Pacific/Auckland`, `Australia/Sydney`, `America/New_York`\n"
                "• Common abbreviations: `NZT`, `AEST`, `UTC`, `EST`, `PST`\n\n"
                "**Defaulting to server timezone for now.** You can set your timezone again anytime.",
                ephemeral=True,
            )

        set_user_timezone(interaction.guild.id, interaction.user.id, iana)

        await interaction.response.send_message(
            f"✅ Your timezone has been set to **{iana}**",
            ephemeral=True,
        )


# ------------------------------------------------------------
# Modal: Set Server Timezone
# ------------------------------------------------------------
class SetServerTimezoneModal(discord.ui.Modal, title="Set Server Timezone"):
    timezone = discord.ui.TextInput(
        label="Server Timezone",
        placeholder="e.g. Pacific/Auckland, Australia/Sydney, NZT, AEST",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await is_leadership(interaction):
            return await interaction.response.send_message(
                "❌ Only leadership can change the server timezone.",
                ephemeral=True,
            )

        tz_text = self.timezone.value.strip()
        iana = normalize_timezone_input(tz_text)

        if not iana:
            return await interaction.response.send_message(
                f"❌ **Invalid timezone**: `{tz_text}`\n\n"
                "Please use a valid timezone format:\n"
                "• IANA format: `Pacific/Auckland`, `Australia/Sydney`, `America/New_York`\n"
                "• Common abbreviations: `NZT`, `AEST`, `UTC`, `EST`, `PST`\n\n"
                "Server timezone was not changed.",
                ephemeral=True,
            )

        set_server_timezone_text(interaction.guild.id, iana)

        await interaction.response.send_message(
            f"✅ Server timezone updated to **{iana}**",
            ephemeral=True,
        )


# ------------------------------------------------------------
# Modal: Broadcast Message (Owner Only)
# ------------------------------------------------------------
class BroadcastModal(discord.ui.Modal, title="📢 Broadcast to All Servers"):
    message = discord.ui.TextInput(
        label="Message",
        placeholder="Your announcement to all servers...",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Send broadcast to all servers."""
        # Double-check owner status
        if not is_owner(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Only bot owners can broadcast messages.",
                ephemeral=True
            )
        
        await interaction.response.defer(ephemeral=True)
        
        message_text = self.message.value.strip()
        
        # Prepare broadcast embed
        embed = discord.Embed(
            title="📢 Bot Announcement",
            description=message_text,
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        # No footer - message only from bot
        
        # Track success/failure
        success_count = 0
        fail_count = 0
        guilds_list = []
        
        # Send to all guilds
        for guild in bot.guilds:
            try:
                # Get the CMI channel for this guild
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute(
                    "SELECT cmi_channel_id FROM guild_channels WHERE guild_id = ?",
                    (guild.id,)
                )
                row = cur.fetchone()
                conn.close()
                
                # Determine target channel
                target_channel = None
                if row and row["cmi_channel_id"]:
                    target_channel = guild.get_channel(row["cmi_channel_id"])
                
                # Fallback to first text channel if no CMI channel set
                if not target_channel:
                    target_channel = next(
                        (ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages),
                        None
                    )
                
                if target_channel:
                    await target_channel.send(embed=embed)
                    success_count += 1
                    guilds_list.append(f"✅ {guild.name}")
                else:
                    fail_count += 1
                    guilds_list.append(f"❌ {guild.name} (no accessible channel)")
                    
            except Exception as e:
                fail_count += 1
                guilds_list.append(f"❌ {guild.name} ({str(e)[:30]})")
                logging.error(f"Failed to broadcast to {guild.name}: {e}")
        
        # Send summary to user
        summary_embed = discord.Embed(
            title="📊 Broadcast Summary",
            description=f"**Message:** {message_text[:100]}{'...' if len(message_text) > 100 else ''}",
            color=discord.Color.green() if fail_count == 0 else discord.Color.orange()
        )
        summary_embed.add_field(
            name="Statistics",
            value=f"✅ Success: {success_count}\n❌ Failed: {fail_count}\n📊 Total: {len(bot.guilds)}",
            inline=False
        )
        
        # Create view with "Show Servers" button
        view = BroadcastSummaryView(guilds_list)
        
        await interaction.followup.send(embed=summary_embed, view=view, ephemeral=True)
        logging.info(f"Broadcast by {interaction.user} completed: {success_count} success, {fail_count} failed")


# ------------------------------------------------------------
# View: Broadcast Summary (Show Servers Button)
# ------------------------------------------------------------
class BroadcastSummaryView(discord.ui.View):
    def __init__(self, guilds_list: list):
        super().__init__(timeout=300)  # 5 minute timeout
        self.guilds_list = guilds_list
    
    @discord.ui.button(label="Show Servers", style=discord.ButtonStyle.secondary)
    async def show_servers(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Split into chunks if too long
        guilds_text = "\n".join(self.guilds_list)
        
        # Discord embed field has 1024 char limit, description has 4096 limit
        if len(guilds_text) > 4000:
            # Split into multiple messages
            chunks = []
            current_chunk = []
            current_length = 0
            
            for guild_line in self.guilds_list:
                if current_length + len(guild_line) + 1 > 3900:  # Leave some buffer
                    chunks.append("\n".join(current_chunk))
                    current_chunk = [guild_line]
                    current_length = len(guild_line)
                else:
                    current_chunk.append(guild_line)
                    current_length += len(guild_line) + 1
            
            if current_chunk:
                chunks.append("\n".join(current_chunk))
            
            # Send first chunk as response
            embed = discord.Embed(
                title="📋 Server List (Part 1)",
                description=chunks[0],
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Send remaining chunks as follow-ups
            for i, chunk in enumerate(chunks[1:], start=2):
                embed = discord.Embed(
                    title=f"📋 Server List (Part {i})",
                    description=chunk,
                    color=discord.Color.blue()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # Single message
            embed = discord.Embed(
                title="📋 Server List",
                description=guilds_text if guilds_text else "No servers",
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)


# ------------------------------------------------------------
# Modal: Set Nickname Prefix
# ------------------------------------------------------------
class SetNicknamePrefixModal(discord.ui.Modal, title="Set CMI Nickname Prefix"):
    prefix = discord.ui.TextInput(
        label="Prefix",
        placeholder="[CMI]",
        required=True,
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction):
        new_prefix = self.prefix.value.strip()

        if not new_prefix:
            return await interaction.response.send_message(
                "Prefix cannot be empty.",
                ephemeral=True,
            )

        set_nickname_prefix(interaction.guild.id, new_prefix)

        await interaction.response.send_message(
            f"Nickname prefix updated to: **{new_prefix}**",
            ephemeral=True,
        )


# ------------------------------------------------------------
# Modal: Daily CMI Report Settings
# ------------------------------------------------------------
class DailyReportSettingsModal(discord.ui.Modal):
    def __init__(self, guild_id: int):
        super().__init__(title="Daily CMI Report Settings")
        self.guild_id = guild_id
        
        self.enabled = discord.ui.TextInput(
            label="Enabled (yes/no, empty = keep current)",
            placeholder="yes or no",
            required=False,
            max_length=3,
        )
        
        self.report_hour = discord.ui.TextInput(
            label="Report Hour (0-23, empty = keep current)",
            placeholder="8",
            required=False,
            max_length=2,
        )
        
        self.channel = discord.ui.TextInput(
            label="Channel ID or name (empty = keep current)",
            placeholder="e.g., cmi-channel or 123456789",
            required=False,
        )
        
        self.add_item(self.enabled)
        self.add_item(self.report_hour)
        self.add_item(self.channel)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await is_leadership(interaction):
            return await interaction.response.send_message(
                "❌ Only leadership can change report settings.",
                ephemeral=True,
            )

        # Load current settings
        current_enabled, current_channel_id, current_hour = get_daily_report_settings(self.guild_id)
        
        # Parse enabled (optional - keep current if not provided)
        enabled = current_enabled
        if self.enabled.value and self.enabled.value.strip():
            enabled_text = self.enabled.value.strip().lower()
            if enabled_text in ["yes", "y", "true", "1", "on"]:
                enabled = True
            elif enabled_text in ["no", "n", "false", "0", "off"]:
                enabled = False
            else:
                return await interaction.response.send_message(
                    "❌ Please enter 'yes' or 'no' for Enabled (or leave empty to keep current).",
                    ephemeral=True,
                )

        # Parse report hour (optional - keep current if not provided)
        report_hour = current_hour
        if self.report_hour.value and self.report_hour.value.strip():
            hour_text = self.report_hour.value.strip()
            try:
                report_hour = int(hour_text)
                if not 0 <= report_hour <= 23:
                    raise ValueError()
            except ValueError:
                return await interaction.response.send_message(
                    "❌ Report Hour must be a number between 0 and 23.",
                    ephemeral=True,
                )

        # Parse channel (optional - keep current if not provided, supports ID or name)
        channel_id = current_channel_id
        if self.channel.value and self.channel.value.strip():
            text = self.channel.value.strip()
            
            channel = None
            if text.isdigit():
                channel = interaction.guild.get_channel(int(text))
            if not channel:
                for ch in interaction.guild.text_channels:
                    if ch.name == text:
                        channel = ch
                        break

            if not channel or not isinstance(channel, discord.TextChannel):
                return await interaction.response.send_message(
                    "❌ I couldn't find a text channel with that ID or exact name.",
                    ephemeral=True,
                )
            
            channel_id = channel.id

        # Save settings
        set_daily_report_settings(interaction.guild.id, enabled, channel_id, report_hour)

        # Build response with current values
        status = "enabled" if enabled else "disabled"
        hour_12 = report_hour % 12 or 12
        am_pm = "AM" if report_hour < 12 else "PM"
        
        response_lines = [
            f"✅ Daily CMI Report settings updated:",
            f"**Status:** {status.capitalize()}",
            f"**Report Hour:** {report_hour}:00 ({hour_12} {am_pm}) in server timezone",
        ]
        
        if channel_id:
            response_lines.append(f"**Channel:** <#{channel_id}>")
        else:
            response_lines.append("**Channel:** CMI channel (default)")
        
        response_lines.append(f"\n_Current values: Enabled={status}, Hour={report_hour}_")

        await interaction.response.send_message(
            "\n".join(response_lines),
            ephemeral=True,
        )


# ------------------------------------------------------------
# Modal: Set Away Role
# ------------------------------------------------------------
class SetAwayRoleModal(discord.ui.Modal, title="Set Away Role"):
    role_id_or_name = discord.ui.TextInput(
        label="Role ID or exact role name",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await is_leadership(interaction):
            return await interaction.response.send_message(
                "❌ Only leadership can change the away role.",
                ephemeral=True,
            )

        text = self.role_id_or_name.value.strip()

        role = None
        if text.isdigit():
            role = interaction.guild.get_role(int(text))
        if not role:
            role = discord.utils.get(interaction.guild.roles, name=text)

        if not role:
            return await interaction.response.send_message(
                "❌ I couldn't find a role with that ID or exact name.",
                ephemeral=True,
            )

        set_away_role_id(interaction.guild.id, role.id)

        await interaction.response.send_message(
            f"✅ Away role set to {role.mention}.\n"
            "Users with an active CMI will be given this role automatically.",
            ephemeral=True,
        )


# ------------------------------------------------------------
# Modal: Set CMI Channel
# ------------------------------------------------------------
class SetCMIChannelModal(discord.ui.Modal, title="Set CMI Channel"):
    channel_id_or_name = discord.ui.TextInput(
        label="Channel ID or exact channel name",
        required=True,
        max_length=100,
    )

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await is_leadership(interaction):
            return await interaction.response.send_message(
                "❌ Only leadership can change the CMI channel.",
                ephemeral=True,
            )

        text = self.channel_id_or_name.value.strip()

        channel = None
        if text.isdigit():
            channel = interaction.guild.get_channel(int(text))
        if not channel:
            for ch in interaction.guild.text_channels:
                if ch.name == text:
                    channel = ch
                    break

        if not channel or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                "❌ I couldn't find a text channel with that ID or exact name.",
                ephemeral=True,
            )

        set_cmi_channel_id(interaction.guild.id, channel.id)

        await interaction.response.send_message(
            f"✅ CMI commands are now restricted to {channel.mention}.",
            ephemeral=True,
        )


# ============================================================
# Section 8 — Per‑CMI UI Components (Edit, Cancel, Return Early)
# ============================================================

# ------------------------------------------------------------
# Modal: Edit an existing CMI
# ------------------------------------------------------------
class CMIEditModal(discord.ui.Modal):
    def __init__(
        self,
        cmi_id: int,
        owner_id: int,
        guild_id: int,
        initial_leave_dt: datetime | None,
        initial_return_dt: datetime | None,
        initial_reason: str | None,
        initial_tz_label: str | None,
    ):
        super().__init__(title=f"Edit CMI #{cmi_id}")

        self.cmi_id = cmi_id
        self.owner_id = owner_id
        self.guild_id = guild_id

        # Pre-fill fields
        leave_date_str = initial_leave_dt.strftime("%d/%m/%Y") if initial_leave_dt else ""
        leave_time_str = initial_leave_dt.strftime("%H:%M") if initial_leave_dt else ""
        return_date_str = initial_return_dt.strftime("%d/%m/%Y") if initial_return_dt else ""
        return_time_str = initial_return_dt.strftime("%H:%M") if initial_return_dt else ""

        self.leave_date = discord.ui.TextInput(
            label="Leave date (e.g. 29/12/2025, 29 Dec)",
            required=False,
            default=leave_date_str,
            max_length=50,
        )
        self.leave_time = discord.ui.TextInput(
            label="Leave time (e.g. 9, 09:00, 9am)",
            required=False,
            default=leave_time_str,
            max_length=20,
        )
        self.return_date = discord.ui.TextInput(
            label="Return date (optional)",
            required=False,
            default=return_date_str,
            max_length=50,
        )
        self.return_time = discord.ui.TextInput(
            label="Return time (optional)",
            required=False,
            default=return_time_str,
            max_length=20,
        )
        self.reason = discord.ui.TextInput(
            label="Reason (optional)",
            required=False,
            default=initial_reason or "",
            style=discord.TextStyle.paragraph,
            max_length=500,
        )

        self.add_item(self.leave_date)
        self.add_item(self.leave_time)
        self.add_item(self.return_date)
        self.add_item(self.return_time)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        # Fetch existing CMI
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, guild_id, leave_dt, return_dt, reason, timezone_label
            FROM cmi_entries
            WHERE guild_id = ? AND id = ?
            """,
            (self.guild_id, self.cmi_id),
        )
        row = cur.fetchone()

        if not row:
            conn.close()
            return await interaction.response.send_message(
                "❌ This CMI no longer exists.", ephemeral=True
            )

        cmi_owner_id = row["user_id"]

        # Permission check
        user_is_leadership = await is_leadership(interaction)
        if interaction.user.id != cmi_owner_id and not user_is_leadership:
            conn.close()
            return await interaction.response.send_message(
                "❌ You can only edit your own CMIs. Leadership can edit any.",
                ephemeral=True,
            )

        # Parse old values
        try:
            old_leave_dt = datetime.fromisoformat(row["leave_dt"])
        except Exception:
            conn.close()
            return await interaction.response.send_message(
                "❌ The existing CMI has corrupted data and cannot be edited.",
                ephemeral=True,
            )

        try:
            old_return_dt = (
                datetime.fromisoformat(row["return_dt"])
                if row["return_dt"]
                else None
            )
        except Exception:
            old_return_dt = None

        old_reason = row["reason"]
        old_tz_label = row["timezone_label"] or ""

        # Read modal inputs
        leave_date_input = self.leave_date.value.strip() if self.leave_date.value else ""
        leave_time_input = self.leave_time.value.strip() if self.leave_time.value else ""
        return_date_input = self.return_date.value.strip() if self.return_date.value else ""
        return_time_input = self.return_time.value.strip() if self.return_time.value else ""
        reason_input = self.reason.value.strip() if self.reason.value else ""

        # Check if user is intentionally clearing leave (both fields empty = start now)
        clearing_leave = (not leave_date_input and not leave_time_input)
        
        # Check if user is intentionally clearing return (both fields empty = open-ended)
        clearing_return = (not return_date_input and not return_time_input)

        changing_dates = any([
            leave_date_input,
            leave_time_input,
            return_date_input,
            return_time_input,
            clearing_leave,
            clearing_return,
        ])
        
        # Track if individual fields were explicitly cleared (for single field edits)
        # If a field has empty string but the other field has content, treat empty as "use default"
        clear_leave_time_only = (leave_date_input and not leave_time_input)
        clear_return_time_only = (return_date_input and not return_time_input)

        # Resolve timezone
        effective_tz, _ = resolve_effective_timezone(
            self.guild_id,
            cmi_owner_id,
            None,
        )
        tz_info = ZoneInfo(effective_tz)

        leave_dt = old_leave_dt
        return_dt = old_return_dt

        # If dates are being changed, rebuild them
        if changing_dates:
            logging.info(
                f"Edit CMI #{self.cmi_id}: inputs - leave_date={leave_date_input!r}, leave_time={leave_time_input!r}, return_date={return_date_input!r}, return_time={return_time_input!r}, clearing_leave={clearing_leave}, clearing_return={clearing_return}"
            )

            # Handle leave date/time
            if clearing_leave:
                # Both leave fields empty = start immediately (now)
                leave_dt = datetime.now(tz_info)
            elif leave_date_input or leave_time_input:
                # At least one field has input - handle each independently
                
                # Leave date: if provided, parse it; if empty, use today
                if leave_date_input:
                    parsed_ld = parse_date(leave_date_input, tz_info)
                    if not parsed_ld:
                        conn.close()
                        return await interaction.response.send_message(
                            "❌ I couldn't understand your new leave date.",
                            ephemeral=True,
                        )
                    ld = parsed_ld
                else:
                    # User deleted the date field = default to today
                    ld = date.today()

                # Leave time: if provided, parse it; if empty, use 00:00
                if leave_time_input:
                    parsed_lt = parse_time(leave_time_input)
                    if not parsed_lt:
                        conn.close()
                        return await interaction.response.send_message(
                            "❌ I couldn't understand your new leave time.",
                            ephemeral=True,
                        )
                    lt = parsed_lt
                else:
                    # User deleted the time field = default to 00:00
                    lt = time(0, 0)

                leave_dt = datetime.combine(ld, lt).replace(tzinfo=tz_info)
            else:
                # Neither field touched = keep old leave date/time
                leave_dt = old_leave_dt

            # Handle return date/time
            if clearing_return:
                # Both return fields empty = open-ended CMI
                return_dt = None
            elif return_date_input or return_time_input:
                # At least one field has input - handle each independently
                
                # Return date: if provided, parse it; if empty, use leave date
                if return_date_input:
                    parsed_rd = parse_date(return_date_input, tz_info)
                    if not parsed_rd:
                        conn.close()
                        return await interaction.response.send_message(
                            "❌ I couldn't understand your new return date.",
                            ephemeral=True,
                        )
                    rd = parsed_rd
                else:
                    # User deleted the date field = default to leave date
                    rd = leave_dt.date()

                # Return time: if provided, parse it; if empty, use 00:00
                if return_time_input:
                    parsed_rt = parse_time(return_time_input)
                    if not parsed_rt:
                        conn.close()
                        return await interaction.response.send_message(
                            "❌ I couldn't understand your new return time.",
                            ephemeral=True,
                        )
                    rt = parsed_rt
                else:
                    # User deleted the time field = default to 00:00 (start of day)
                    rt = time(0, 0)

                return_dt = datetime.combine(rd, rt).replace(tzinfo=tz_info)
            else:
                # Neither field touched = keep old return date/time
                return_dt = old_return_dt

        # Keep existing timezone label
        tz_label = old_tz_label or f"Server Timezone: {effective_tz}"

        # Overlap detection
        has_overlap, conflict = await has_overlapping_cmi(
            self.guild_id,
            cmi_owner_id,
            leave_dt,
            return_dt,
            exclude_id=self.cmi_id,
        )

        if has_overlap:
            conflict_leave_str = conflict["leave_dt"].astimezone(tz_info).strftime(
                "%d/%m/%Y %H:%M"
            )
            if conflict["return_dt"]:
                conflict_return_str = conflict["return_dt"].astimezone(
                    tz_info
                ).strftime("%d/%m/%Y %H:%M")
                conflict_range = f"{conflict_leave_str} → {conflict_return_str}"
            else:
                conflict_range = f"{conflict_leave_str} → Until further notice"

            conflict_reason = (
                f"Reason: {conflict['reason']}"
                if conflict["reason"]
                else "No reason provided."
            )

            conn.close()
            return await interaction.response.send_message(
                "❌ This edited CMI would overlap with an existing one.\n"
                f"Existing CMI (ID {conflict['id']}): {conflict_range}\n"
                f"{conflict_reason}",
                ephemeral=True,
            )

        # Final reason
        new_reason = reason_input if reason_input != "" else old_reason

        logging.info(
            f"Edit CMI #{self.cmi_id}: About to save - leave_dt={leave_dt.isoformat()}, return_dt={return_dt.isoformat() if return_dt else None}, reason={new_reason!r}"
        )

        # Update DB
        cur.execute(
            """
            UPDATE cmi_entries
            SET leave_dt = ?, return_dt = ?, reason = ?, timezone_label = ?
            WHERE guild_id = ? AND id = ?
            """,
            (
                leave_dt.isoformat(),
                return_dt.isoformat() if return_dt else None,
                new_reason,
                tz_label,
                self.guild_id,
                self.cmi_id,
            ),
        )
        conn.commit()
        
        # Verify the update actually happened
        cur = conn.cursor()
        cur.execute(
            "SELECT leave_dt, return_dt, reason FROM cmi_entries WHERE guild_id = ? AND id = ?",
            (self.guild_id, self.cmi_id)
        )
        verify_row = cur.fetchone()
        if verify_row:
            logging.info(
                f"Edit CMI #{self.cmi_id}: Verified in DB - leave_dt={verify_row['leave_dt']}, return_dt={verify_row['return_dt']}, reason={verify_row['reason']!r}"
            )
        conn.close()

        # Recompute away role
        if interaction.guild:
            await recompute_away_role_for_user(interaction.guild, cmi_owner_id)

        # Build confirmation message with user name and new CMI details
        member = interaction.guild.get_member(cmi_owner_id)
        user_name = member.display_name if member else f"User {cmi_owner_id}"
        
        leave_str = leave_dt.astimezone(tz_info).strftime("%d/%m/%Y %H:%M")
        leave_ts = to_discord_timestamp(leave_dt)
        
        if return_dt:
            return_str = return_dt.astimezone(tz_info).strftime("%d/%m/%Y %H:%M")
            return_ts = to_discord_timestamp(return_dt)
            time_range = f"{leave_ts} → {return_ts}"
        else:
            time_range = f"{leave_ts} → Until further notice"
        
        reason_text = f"Reason: {new_reason}" if new_reason else "No reason provided."
        
        await interaction.response.send_message(
            f"✅ **{user_name}'s CMI updated**\n"
            f"{time_range}\n"
            f"{reason_text}",
            ephemeral=False,
        )


# ------------------------------------------------------------
# View: Confirm CMI Cancellation
# ------------------------------------------------------------
class CMIConfirmCancelView(discord.ui.View):
    def __init__(self, cmi_id: int, owner_id: int, guild_id: int):
        super().__init__(timeout=30)
        self.cmi_id = cmi_id
        self.owner_id = owner_id
        self.guild_id = guild_id

    @discord.ui.button(label="Yes, cancel", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_is_leadership = await is_leadership(interaction)
        if interaction.user.id != self.owner_id and not user_is_leadership:
            return await interaction.response.send_message(
                "❌ You can only cancel your own CMIs. Leadership can cancel any.",
                ephemeral=True,
            )

        # Fetch CMI details before deleting
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT leave_dt, return_dt, reason, timezone_label FROM cmi_entries WHERE guild_id = ? AND id = ?",
            (self.guild_id, self.cmi_id),
        )
        row = cur.fetchone()
        
        if not row:
            conn.close()
            return await interaction.response.send_message(
                "❌ CMI not found.",
                ephemeral=True,
            )
        
        # Parse CMI details
        try:
            leave_dt = datetime.fromisoformat(row["leave_dt"])
        except Exception:
            leave_dt = None
        
        try:
            return_dt = datetime.fromisoformat(row["return_dt"]) if row["return_dt"] else None
        except Exception:
            return_dt = None
        
        tz_label = row["timezone_label"] or "No timezone specified"
        reason = row["reason"]
        
        # Get server timezone for display
        server_tz_name = get_server_timezone_text(self.guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        
        # Delete the CMI
        cur.execute(
            "DELETE FROM cmi_entries WHERE guild_id = ? and id = ?",
            (self.guild_id, self.cmi_id),
        )
        conn.commit()
        conn.close()

        if interaction.guild:
            await recompute_away_role_for_user(interaction.guild, self.owner_id)

        # Get user's display name
        member = interaction.guild.get_member(self.owner_id)
        user_name = member.display_name if member else f"User {self.owner_id}"
        
        # Format cancelled CMI with strikethrough
        if leave_dt:
            leave_ts = to_discord_timestamp(leave_dt)
            if return_dt:
                return_ts = to_discord_timestamp(return_dt)
                time_range = f"~~{leave_ts} → {return_ts}~~"
            else:
                time_range = f"~~{leave_ts} → Until further notice~~"
        else:
            time_range = "~~CMI details unavailable~~"
        
        reason_text = f"~~Reason: {reason}~~" if reason else "~~No reason provided.~~"
        
        # Send public message then edit original to remove buttons
        await interaction.response.send_message(
            f"🗑️ **{user_name}'s CMI has been cancelled**\n"
            f"{time_range}\n"
            f"{reason_text}",
            ephemeral=False,
        )
        try:
            await interaction.message.edit(view=None)
        except Exception:
            pass

    @discord.ui.button(label="No, keep it", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="❎ CMI cancellation aborted.",
            view=None,
        )


# ------------------------------------------------------------
# View: Per‑CMI Action Buttons
# ------------------------------------------------------------
class CMIEntryView(discord.ui.View):
    def __init__(self, cmi_id: int, owner_id: int, guild_id: int):
        super().__init__(timeout=None)
        self.cmi_id = cmi_id
        self.owner_id = owner_id
        self.guild_id = guild_id

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary)
    async def edit_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_is_leadership = await is_leadership(interaction)
        if interaction.user.id != self.owner_id and not user_is_leadership:
            return await interaction.response.send_message(
                "❌ You can only edit your own CMIs. Leadership can edit any.",
                ephemeral=True,
            )

        # Fetch CMI
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, guild_id, leave_dt, return_dt, reason, timezone_label
            FROM cmi_entries
            WHERE guild_id = ? AND id = ?
            """,
            (self.guild_id, self.cmi_id),
        )
        row = cur.fetchone()
        conn.close()

        if not row:
            return await interaction.response.send_message(
                "❌ This CMI no longer exists.",
                ephemeral=True,
            )

        try:
            leave_dt = datetime.fromisoformat(row["leave_dt"])
        except Exception:
            leave_dt = None

        try:
            return_dt = (
                datetime.fromisoformat(row["return_dt"])
                if row["return_dt"]
                else None
            )
        except Exception:
            return_dt = None

        modal = CMIEditModal(
            cmi_id=self.cmi_id,
            owner_id=self.owner_id,
            guild_id=self.guild_id,
            initial_leave_dt=leave_dt,
            initial_return_dt=return_dt,
            initial_reason=row["reason"],
            initial_tz_label=row["timezone_label"],
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Cancel CMI", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_is_leadership = await is_leadership(interaction)
        if interaction.user.id != self.owner_id and not user_is_leadership:
            return await interaction.response.send_message(
                "❌ You can only cancel your own CMIs. Leadership can cancel any.",
                ephemeral=True,
            )

        view = CMIConfirmCancelView(
            cmi_id=self.cmi_id,
            owner_id=self.owner_id,
            guild_id=self.guild_id,
        )
        await interaction.response.send_message(
            f"Are you sure you want to cancel CMI #{self.cmi_id}?",
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Return early", style=discord.ButtonStyle.success)
    async def return_early_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_is_leadership = await is_leadership(interaction)
        if interaction.user.id != self.owner_id and not user_is_leadership:
            return await interaction.response.send_message(
                "❌ You can only return early from your own CMIs. Leadership can modify any.",
                ephemeral=True,
            )

        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )

        guild = interaction.guild
        guild_id = guild.id

        # Server timezone
        server_tz_name = get_server_timezone_text(guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        now = datetime.now(server_tz)

        # Fetch CMI
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT leave_dt, return_dt
            FROM cmi_entries
            WHERE guild_id = ? AND id = ? AND user_id = ?
            """,
            (guild_id, self.cmi_id, self.owner_id),
        )
        row = cur.fetchone()
        conn.close()

        if not row:
            return await interaction.response.send_message(
                "❌ This CMI no longer exists.",
                ephemeral=True,
            )

        try:
            leave_dt = datetime.fromisoformat(row["leave_dt"])
        except Exception:
            return await interaction.response.send_message(
                "❌ This CMI has corrupted data and cannot be updated.",
                ephemeral=True,
            )

        try:
            return_dt = (
                datetime.fromisoformat(row["return_dt"])
                if row["return_dt"]
                else None
            )
        except Exception:
            return_dt = None

        leave_local = leave_dt.astimezone(server_tz)
        return_local = return_dt.astimezone(server_tz) if return_dt else None

        active = leave_local <= now and (return_local is None or return_local >= now)

        if not active:
            return await interaction.response.send_message(
                "ℹ You have no active CMI to return early from.",
                ephemeral=True,
            )

        # Set return_dt to now
        new_return_dt = now

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE cmi_entries
            SET return_dt = ?
            WHERE guild_id = ? AND id = ?
            """,
            (new_return_dt.isoformat(), guild_id, self.cmi_id),
        )
        conn.commit()
        conn.close()

        await recompute_away_role_for_user(guild, self.owner_id)

        # Get user's display name
        member = guild.get_member(self.owner_id)
        user_name = member.display_name if member else f"User {self.owner_id}"

        await interaction.response.send_message(
            f"✅ You have returned early from your current CMI. Welcome back **{user_name}**!",
            ephemeral=False,
        )
# ============================================================
# Section 9 — Main Menu Views (MainCMIMenuView & LeadershipToolsView)
# ============================================================

# ------------------------------------------------------------
# Main CMI Menu View
# ------------------------------------------------------------
class MainCMIMenuView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int, is_leadership: bool):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id
        self.is_leadership = is_leadership

        # Leadership-only button
        if is_leadership:
            self.add_item(self.LeadershipToolsButton())

    # -------------------------
    # Create CMI
    # -------------------------
    @discord.ui.button(label="Create CMI", style=discord.ButtonStyle.primary)
    async def create_cmi(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await enforce_cmi_channel(interaction):
            return

        modal = CreateCMIModal(target_user=None)
        await interaction.response.send_modal(modal)

    # -------------------------
    # Manage My CMIs
    # -------------------------
    @discord.ui.button(label="Manage My CMIs", style=discord.ButtonStyle.secondary)
    async def manage_my_cmis(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await enforce_cmi_channel(interaction):
            return

        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        await cog.show_manage_cmi_ui(interaction, target_member=interaction.user)

    # -------------------------
    # List CMIs
    # -------------------------
    @discord.ui.button(label="List CMIs", style=discord.ButtonStyle.success)
    async def list_cmis(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await enforce_cmi_channel(interaction):
            return

        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        await cog.show_list(interaction)

    # -------------------------
    # My History
    # -------------------------
    @discord.ui.button(label="My History", style=discord.ButtonStyle.secondary)
    async def my_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await enforce_cmi_channel(interaction):
            return

        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        await cog.show_my_history(interaction)

    # -------------------------
    # Set My Timezone
    # -------------------------
    @discord.ui.button(label="Set My Timezone", style=discord.ButtonStyle.primary)
    async def set_my_timezone(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetUserTimezoneModal()
        await interaction.response.send_modal(modal)

    # -------------------------
    # Leadership Tools Button
    # -------------------------
    class LeadershipToolsButton(discord.ui.Button):
        def __init__(self):
            super().__init__(
                label="Leadership Tools",
                style=discord.ButtonStyle.danger,
            )

        async def callback(self, interaction: discord.Interaction):
            if not await is_leadership(interaction):
                return await interaction.response.send_message(
                    "❌ You are not leadership.",
                    ephemeral=True,
                )

            embed = build_leadership_tools_embed()
            view = LeadershipToolsView(
                guild_id=interaction.guild.id,
                user_id=interaction.user.id,
            )

            await interaction.response.send_message(
                embed=embed,
                view=view,
                ephemeral=True,
            )


# ------------------------------------------------------------
# Leadership Tools View
# ------------------------------------------------------------
class LeadershipToolsView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id

    # -------------------------
    # Set Server Timezone
    # -------------------------
    @discord.ui.button(label="Set Server Timezone", style=discord.ButtonStyle.primary)
    async def set_server_timezone(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetServerTimezoneModal()
        await interaction.response.send_modal(modal)

    # -------------------------
    # Set Away Role
    # -------------------------
    @discord.ui.button(label="Set Away Role", style=discord.ButtonStyle.primary)
    async def set_away_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetAwayRoleModal()
        await interaction.response.send_modal(modal)

    # -------------------------
    # Set CMI Channel
    # -------------------------
    @discord.ui.button(label="Set CMI Channel", style=discord.ButtonStyle.primary)
    async def set_cmi_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetCMIChannelModal()
        await interaction.response.send_modal(modal)

    # -------------------------
    # Manage CMIs for Others
    # -------------------------
    @discord.ui.button(label="Manage CMIs for Others", style=discord.ButtonStyle.secondary)
    async def manage_for_others(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SelectUserForManageCMIModal()
        await interaction.response.send_modal(modal)

    # -------------------------
    # View Previous CMIs
    # -------------------------
    @discord.ui.button(label="View Previous CMIs", style=discord.ButtonStyle.secondary)
    async def view_previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)
        await cog.show_previous_cmis(interaction)

    # -------------------------
    # Set Nickname Prefix
    # -------------------------
    @discord.ui.button(label="Set Nickname Prefix", style=discord.ButtonStyle.danger, custom_id="cmi_set_nick_prefix")
    async def set_prefix(self, interaction: discord.Interaction, button: discord.ui.Button):
        # This button is handled by on_interaction
        pass
# ============================================================
# Section 10 — UI Classes (Final Menus + User Selection)
# ============================================================

# ------------------------------------------------------------
# Support Embed
# ------------------------------------------------------------
def build_support_embed():
    embed = discord.Embed(
        title="💛 Support the Bot",
        description=(
            "Tips help my work (and myself) keep going.\n"
            "They are not required but are appreciated.\n\n"
            "[ko-fi.com/savxo](https://ko-fi.com/savxo)"
        ),
        color=discord.Color.gold(),
    )
    return embed


# ------------------------------------------------------------
# User Help Embed
# ------------------------------------------------------------
def build_help_embed():
    embed = discord.Embed(
        title="📘 How to Use the CMI System",
        description=(
            "Welcome to the CMI (absence) system! Here's everything you need to know.\n\n"
            "**🚀 Quick Start**\n"
            "1. **Set your timezone first** using 'Set My Timezone' — this ensures all dates/times are correct for you\n"
            "2. Create your first CMI with 'Create CMI'\n"
            "3. That's it! The bot handles the rest\n\n"
            "**🎯 Main Buttons**\n"
            "• **Create CMI** — Submit a new absence. You'll pick dates/times and add an optional reason\n"
            "• **Manage My CMIs** — View, edit, or cancel your active CMIs. You can also return early from here\n"
            "• **My History** — See all your past CMIs for record keeping\n"
            "• **Set My Timezone** — Configure your timezone (e.g., 'Sydney', 'AEDT', 'UTC+10'). This overrides the server default\n"
            "• **List CMIs** — See everyone's current and upcoming absences in one place\n"
            "• **Check Server Timezone** — View the server's default timezone (used if you haven't set yours)\n\n"
            "**📅 Date & Time Tips**\n"
            "You can enter dates as:\n"
            "• 'Today' or 'Tomorrow'\n"
            "• 'Jan 4' or '9 Mar' (assumes current year or next occurrence)\n"
            "• DD/MM/YY or DD/MM/YYYY (e.g., '15/03/25')\n"
            "• Times as 24hr (14:30) or 12hr (2:30 PM)\n\n"
            "**Optional Fields:**\n"
            "• Leave **Leave Date/Time empty** for an immediate start\n"
            "• Leave **Return Date/Time empty** for an indefinite CMI (you can return early manually)\n"
            "• **Reason** is always optional\n\n"
            "**🔔 What Happens Automatically**\n"
            "When your CMI starts:\n"
            "• You get the 'CMI' role (if configured by leadership)\n"
            "• Your nickname gets a prefix (e.g., '[CMI]') so others know you're away\n"
            "• Leadership can customize what the role and prefix show as\n"
            "\n"
            "When your CMI ends (or you return early):\n"
            "• The role and prefix are automatically removed\n"
            "• CMIs with a return date older than 90 days will be automatically deleted\n\n"
            "**❓ Need More Help?**\n"
            "Just type `/cmi` again to reopen the menu anytime!"
        ),
        color=discord.Color.blue(),
    )
    return embed


# ------------------------------------------------------------
# Leadership Help Embed
# ------------------------------------------------------------
def build_leadership_help_embed():
    embed = discord.Embed(
        title="🛠️ Leadership Help",
        description=(
            "Complete guide to managing the CMI system for your server.\n\n"
            "**📋 Leadership Tools Overview**\n"
            "Access these tools via the 'Leadership Tools' button. You need 'Administrator' or 'Manage Guild' permission, or be granted access via 'Manage Bot Perms'.\n\n"
            "**👥 Create/Manage CMI for Others**\n"
            "• **Create CMI for Others** — Submit absences on behalf of any member (useful for known absences)\n"
            "• **Manage CMI for Others** — Search for a member, then view/edit/cancel their active CMIs\n"
            "These tools use the same flows as regular users, just targeted at other members.\n\n"
            "**📜 Show Previous CMIs**\n"
            "View all past CMIs across the entire server (sorted by most recent first). Great for record-keeping and reviewing absence patterns.\n\n"
            "**📊 Export CMIs to CSV**\n"
            "Download a complete CSV export of all CMI entries for your server. The export includes:\n"
            "• User ID and Username\n"
            "• Leave and Return dates/times (or 'Indefinite' for open-ended CMIs)\n"
            "• Reason for absence\n"
            "• Status (Scheduled, Active, or Completed)\n"
            "• Timezone used for the CMI\n"
            "• Created date\n"
            "• Days Away (total duration or 'Indefinite')\n"
            "• Created By (Self if user created it, or the leadership member's name)\n\n"
            "Perfect for tracking absence patterns, auditing, or external record-keeping.\n\n"
            "**📅 Daily CMI Report Settings**\n"
            "Automatically send a daily report of current and upcoming CMIs to your server. Configure:\n"
            "• **Enabled** — Turn the daily report on or off (yes/no)\n"
            "• **Report Hour** — What hour to send the report (0-23, in server timezone, default 8 AM)\n"
            "• **Channel** — Which channel to send to (leave empty to use the CMI channel)\n\n"
            "The report shows all active CMIs and any scheduled to start within the next 7 days. Uses the same limit as the 'List CMIs' command to keep messages manageable.\n\n"
            "**⚙️ Server Settings**\n"
            "Configure how the CMI system works for your server:\n\n"
            "• **Set Server Timezone** — Default timezone for all users who haven't set their own (e.g., 'Sydney', 'AEDT', 'UTC+10'). Affects how dates/times are displayed\n"
            "• **Set CMI Channel** — Restrict `/cmi` command to one channel (optional — leave unset to allow everywhere)\n"
            "• **Set CMI Role** — The role applied to users when their CMI is active (e.g., 'Away'). This role is auto-assigned/removed\n"
            "• **Set CMI Prefix** — Nickname prefix for active CMIs (e.g., '[CMI]' or '[Away]'). Auto-added/removed with the role\n\n"
            "**🔐 Manage Bot Perms**\n"
            "Grant leadership access to users or roles that don't have 'Administrator' or 'Manage Guild' permissions. Perfect for trusted moderators or team leads.\n\n"
            "**⚠️ Important Setup Notes**\n"
            "**Role Hierarchy:** The bot's role must be positioned **above** the CMI role in Server Settings → Roles. If the bot is below the CMI role, it cannot assign roles or modify nicknames. This is a Discord limitation.\n\n"
            "**Nickname Prefixes & Admins:** Due to Discord's hierarchy rules, the bot cannot modify nicknames of members whose **highest role** is above the bot's role. This commonly affects server owners and high-level admins. Solutions:\n"
            "• Move the bot role higher (above admin roles if possible)\n"
            "• Accept that prefixes won't apply to those users (the role will still be assigned)\n\n"
            "**Permissions Required:** The bot needs 'Manage Roles' and 'Manage Nicknames' permissions to function fully.\n\n"
            "**Channel Deletion Handling:** If configured channels are deleted:\n"
            "• **Daily Report Channel** deleted → Reports automatically fallback to the CMI channel\n"
            "• **CMI Channel** deleted → `/cmi` command becomes available in all channels (leadership will receive a notification)\n"
            "• Daily reports will fallback to any accessible channel and notify leadership to reconfigure\n\n"
            "**✅ Best Practices**\n"
            "• Set the server timezone early — it's the fallback for all users\n"
            "• Create a dedicated CMI channel to keep things organized (optional)\n"
            "• Choose a clear, visible role name (e.g., 'Away' or 'On Leave')\n"
            "• Keep prefixes short and consistent (e.g., '[CMI]' or '[Away]')\n"
            "• Test the system yourself first before rolling out to the server\n"
            "• Check role hierarchy if nicknames aren't updating properly\n"
            "• Use CSV exports for periodic audits or external tracking\n"
            "• Enable daily reports to keep leadership informed at a glance\n\n"
            "**❓ Need Help?**\n"
            "If something isn't working, check: role hierarchy, bot permissions, and that settings are configured. Most issues come from Discord's role positioning requirements."
        ),
        color=discord.Color.purple(),
    )
    return embed


# ------------------------------------------------------------
# Main Menu Embed
# ------------------------------------------------------------
def build_main_menu_embed(guild, user, is_leadership: bool):
    embed = discord.Embed(
        title="🌿 CMI / Absence Menu",
        description=(
            f"Welcome, **{user.display_name}**!\n"
            "This bot helps streamline your time away so you can touch some grass.\n"
            "Use the buttons below to manage your CMIs quickly and easily."
        ),
        color=discord.Color.blurple(),
    )
    return embed


# ------------------------------------------------------------
# Leadership Tools Embed
# ------------------------------------------------------------
def build_leadership_menu_embed():
    embed = discord.Embed(
        title="🛠️ Leadership Tools",
        description=(
            "These tools allow leadership to manage CMIs across the guild.\n"
            "You can create, edit, and review CMIs for others, and configure bot settings."
        ),
        color=discord.Color.purple(),
    )
    return embed


# ------------------------------------------------------------
# Leadership Tools (detailed) Embed
# ------------------------------------------------------------
def build_leadership_tools_embed() -> discord.Embed:
    return discord.Embed(
        title="🛠️ Leadership Tools",
        color=discord.Color.red(),
        description=(
            "Leadership-only controls for configuring and reviewing CMIs.\n\n"
            "Use the buttons below to:\n"
            "- Set the server's default timezone\n"
            "- Set the away role (for active CMIs)\n"
            "- Set which channel CMIs should be used in\n"
            "- View previous CMIs\n"
            "- Manage CMIs for other members"
        ),
    )


# ------------------------------------------------------------
# Placeholder Modal for Manage Bot Perms
# ------------------------------------------------------------
class ManageBotPermsModal(discord.ui.Modal):
    def __init__(self, target_member: discord.Member | None = None):
        super().__init__(title="Manage Bot Permissions")
        self.target_member = target_member

        self.notes = discord.ui.TextInput(
            label="Notes",
            placeholder="This feature is coming soon.",
            required=False,
        )
        self.add_item(self.notes)

    async def on_submit(self, interaction: discord.Interaction):
        if self.target_member:
            msg = (
                f"Bot permission management for {self.target_member.mention} "
                "is coming soon."
            )
        else:
            msg = "Bot permission management is coming soon."

        await interaction.response.send_message(
            msg,
            ephemeral=True,
        )


# ------------------------------------------------------------
# Manage Bot Perms Menu (roles vs users)
# ------------------------------------------------------------
class BotPermsMenuView(discord.ui.View):
    def __init__(self, cog: "CMI"):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="Role Perms", style=discord.ButtonStyle.primary)
    async def role_perms(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_role_perms_menu(interaction)
        self.stop()

    @discord.ui.button(label="User Perms", style=discord.ButtonStyle.secondary)
    async def user_perms(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_user_perms_menu(interaction)
        self.stop()

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Closed bot perms menu.", ephemeral=True)
        self.stop()


class RolePermsMenuView(discord.ui.View):
    def __init__(self, cog: "CMI"):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="View Current Role Perms", style=discord.ButtonStyle.primary)
    async def view_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.view_role_perms(interaction)
        except Exception as e:
            logging.exception("Error in view_roles button")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Error displaying roles: {e}",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Error displaying roles: {e}",
                        ephemeral=True,
                    )
            except Exception:
                logging.exception("Failed to send error message")

    @discord.ui.button(label="Add Role", style=discord.ButtonStyle.success)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddRolePermModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Remove Role", style=discord.ButtonStyle.danger)
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RemoveRolePermModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start_manage_bot_perms(interaction)
        self.stop()


class UserPermsMenuView(discord.ui.View):
    def __init__(self, cog: "CMI"):
        super().__init__(timeout=120)
        self.cog = cog

    @discord.ui.button(label="View Current User Perms", style=discord.ButtonStyle.primary)
    async def view_users(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await self.cog.view_user_perms(interaction)
        except Exception as e:
            logging.exception("Error in view_users button")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        f"❌ Error displaying users: {e}",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        f"❌ Error displaying users: {e}",
                        ephemeral=True,
                    )
            except Exception:
                logging.exception("Failed to send error message")

    @discord.ui.button(label="Add User", style=discord.ButtonStyle.success)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddUserPermModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Remove User", style=discord.ButtonStyle.danger)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RemoveUserPermModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.start_manage_bot_perms(interaction)
        self.stop()


class AddRolePermModal(discord.ui.Modal, title="Add Role Permission"):
    def __init__(self):
        super().__init__()
        self.role_input = discord.ui.TextInput(
            label="Role Name or ID",
            placeholder="Enter the role name or ID",
            required=True,
        )
        self.add_item(self.role_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )
        await cog.handle_add_role_perm(interaction, self.role_input.value)


class RemoveRolePermModal(discord.ui.Modal, title="Remove Role Permission"):
    def __init__(self):
        super().__init__()
        self.role_input = discord.ui.TextInput(
            label="Role Name or ID",
            placeholder="Enter the role name or ID",
            required=True,
        )
        self.add_item(self.role_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )
        await cog.handle_remove_role_perm(interaction, self.role_input.value)


class AddUserPermModal(discord.ui.Modal, title="Add User Permission"):
    def __init__(self):
        super().__init__()
        self.user_input = discord.ui.TextInput(
            label="User Name or ID",
            placeholder="Enter username, display name, or ID",
            required=True,
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )
        await cog.handle_add_user_perm(interaction, self.user_input.value)


class RemoveUserPermModal(discord.ui.Modal, title="Remove User Permission"):
    def __init__(self):
        super().__init__()
        self.user_input = discord.ui.TextInput(
            label="User Name or ID",
            placeholder="Enter username, display name, or ID",
            required=True,
        )
        self.add_item(self.user_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog: "CMI" = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )
        await cog.handle_remove_user_perm(interaction, self.user_input.value)


# ============================================================
# User Selection UI (Dropdown + Search + Cancel)
# ============================================================

class MemberDropdown(discord.ui.Select):
    """
    Dropdown showing up to 25 members (Display Name — Username).
    The actual resolution and follow-up logic is handled by the CMI cog.
    """

    def __init__(self, guild: discord.Guild, purpose: str, requester_id: int):
        self.guild = guild
        self.purpose = purpose
        self.requester_id = requester_id

        options: list[discord.SelectOption] = []

        members = [
            m
            for m in guild.members
            if not m.bot
        ]
        members.sort(key=lambda m: (m.display_name or m.name).lower())

        for member in members[:25]:
            label = f"{member.display_name} — {member.name}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(member.id),
                )
            )

        placeholder = "Select a member…" if options else "No members available."
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=options or [
                discord.SelectOption(
                    label="No members available",
                    value="none",
                    default=True,
                )
            ],
        )

    async def callback(self, interaction: discord.Interaction):
        # Only the original requester can use this selector
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message(
                "❌ Only the person who opened this selection can use it.",
                ephemeral=True,
            )

        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not self.values or self.values[0] == "none":
            return await interaction.response.send_message(
                "❌ No valid member selected.",
                ephemeral=True,
            )

        member_id = int(self.values[0])
        member = interaction.guild.get_member(member_id)
        if not member:
            return await interaction.response.send_message(
                "❌ That member could not be found.",
                ephemeral=True,
            )

        cog = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        # Delegate to CMI cog to handle the chosen member for this purpose
        await cog.handle_member_selected(interaction, member, self.purpose)


class UserSelectionView(discord.ui.View):
    """
    View that presents:
    - A member dropdown
    - A button to search by name/ID
    - A cancel button
    The heavy lifting is done in the CMI cog.
    """

    def __init__(self, guild: discord.Guild, purpose: str, requester_id: int):
        super().__init__(timeout=60)
        self.guild = guild
        self.purpose = purpose
        self.requester_id = requester_id

        # Add dropdown
        self.add_item(MemberDropdown(guild, purpose, requester_id))

    @discord.ui.button(label="Search by Name or ID", style=discord.ButtonStyle.primary)
    async def search_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message(
                "❌ Only the person who opened this selection can use it.",
                ephemeral=True,
            )

        modal = UserSearchModal(purpose=self.purpose, requester_id=self.requester_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message(
                "❌ Only the person who opened this selection can use it.",
                ephemeral=True,
            )

        # Let the cog know we cancelled (for consistency), but mainly just inform the user.
        cog = interaction.client.get_cog("CMI")
        if cog:
            await cog.handle_member_selection_cancelled(interaction, self.purpose)

        await interaction.response.send_message(
            "Selection cancelled.",
            ephemeral=True,
        )
        self.stop()


class UserSearchModal(discord.ui.Modal, title="Search for a Member"):
    """
    Modal to search by name, nickname, ID, or mention.
    Actual search logic is implemented in the CMI cog.
    """

    def __init__(self, purpose: str, requester_id: int):
        super().__init__()
        self.purpose = purpose
        self.requester_id = requester_id

        self.query = discord.ui.TextInput(
            label="Search",
            placeholder="Name, nickname, username, ID, or mention",
            required=True,
            max_length=100,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.requester_id:
            return await interaction.response.send_message(
                "❌ Only the person who opened this selection can use it.",
                ephemeral=True,
            )

        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        cog = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        # Delegate to cog for actual search + handling
        await cog.handle_user_search_submission(
            interaction=interaction,
            query=self.query.value,
            purpose=self.purpose,
        )


# ============================================================
# Main Menu View
# ============================================================
class MainCMIMenuView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int, is_leadership: bool):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id
        self.is_leadership = is_leadership

        # Hide Leadership Tools button for non-leadership users
        if not is_leadership:
            self.remove_item(self.leadership_tools)
        
        # Hide Broadcast Message button for non-owners
        if not is_owner(user_id):
            self.remove_item(self.broadcast_message)

    # 1. Create CMI
    @discord.ui.button(label="Create CMI", style=discord.ButtonStyle.primary)
    async def create_cmi(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CreateCMIModal()
        await interaction.response.send_modal(modal)

    # 2. Manage My CMIs
    @discord.ui.button(label="Manage My CMIs", style=discord.ButtonStyle.secondary)
    async def manage_my_cmis(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("CMI")
        await cog.show_manage_cmi_ui(interaction, interaction.user)

    # 3. My History
    @discord.ui.button(label="My History", style=discord.ButtonStyle.secondary)
    async def my_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("CMI")
        await cog.show_my_history(interaction)

    # 4. Set My Timezone
    @discord.ui.button(label="Set My Timezone", style=discord.ButtonStyle.secondary)
    async def set_my_timezone(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetUserTimezoneModal()
        await interaction.response.send_modal(modal)

    # 5. List CMIs
    @discord.ui.button(label="List CMIs", style=discord.ButtonStyle.secondary)
    async def list_cmis(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("CMI")
        await cog.show_list(interaction)

    # 6. Check Server Timezone
    @discord.ui.button(label="Check Server Timezone", style=discord.ButtonStyle.secondary)
    async def check_server_timezone(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild.id
        tz = get_server_timezone_text(guild_id)
        await interaction.response.send_message(
            f"🌐 **Server Timezone:** `{tz}`",
            ephemeral=True,
        )

    # 7. Help / How to Use
    @discord.ui.button(label="Help / How to Use", style=discord.ButtonStyle.secondary)
    async def help_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_help_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 8. Support the Bot
    @discord.ui.button(label="Support the Bot", style=discord.ButtonStyle.success)
    async def support_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_support_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 9. Leadership Tools
    @discord.ui.button(label="Leadership Tools", style=discord.ButtonStyle.danger)
    async def leadership_tools(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        embed = build_leadership_menu_embed()
        view = LeadershipToolsView(self.guild_id, self.user_id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # 10. Broadcast Message (Owner Only)
    @discord.ui.button(label="📢 Broadcast Message", style=discord.ButtonStyle.danger, row=4)
    async def broadcast_message(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Double-check owner status
        if not is_owner(interaction.user.id):
            return await interaction.response.send_message(
                "❌ Only bot owners can broadcast messages.",
                ephemeral=True
            )
        
        modal = BroadcastModal()
        await interaction.response.send_modal(modal)


# ============================================================
# Leadership Tools View
# ============================================================
class LeadershipToolsView(discord.ui.View):
    def __init__(self, guild_id: int, user_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.user_id = user_id

    # 1. Return to Main Menu
    @discord.ui.button(label="Return to Main Menu", style=discord.ButtonStyle.secondary)
    async def return_main(self, interaction: discord.Interaction, button: discord.ui.Button):
        is_lead = await is_leadership(interaction)
        embed = build_main_menu_embed(interaction.guild, interaction.user, is_lead)
        view = MainCMIMenuView(self.guild_id, self.user_id, is_lead)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # 2. Create CMI for Others
    @discord.ui.button(label="Create CMI for Others", style=discord.ButtonStyle.primary)
    async def create_for_others(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        # Start the guided selection + create flow
        await cog.start_create_cmi_for_others(interaction)

    # 3. Manage CMI for Others
    @discord.ui.button(label="Manage CMI for Others", style=discord.ButtonStyle.secondary)
    async def manage_for_others(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        # Start the guided selection + manage flow
        await cog.start_manage_cmi_for_others(interaction)

    # 4. Show Previous CMIs
    @discord.ui.button(label="Show Previous CMIs", style=discord.ButtonStyle.secondary)
    async def show_previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        cog = interaction.client.get_cog("CMI")
        await cog.show_previous_cmis(interaction)

    # 5. Set Server Timezone
    @discord.ui.button(label="Set Server Timezone", style=discord.ButtonStyle.secondary)
    async def set_server_timezone(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetServerTimezoneModal()
        await interaction.response.send_modal(modal)

    # 6. Set CMI Channel
    @discord.ui.button(label="Set CMI Channel", style=discord.ButtonStyle.secondary)
    async def set_cmi_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetCMIChannelModal()
        await interaction.response.send_modal(modal)

    # 7. Set CMI Role
    @discord.ui.button(label="Set CMI Role", style=discord.ButtonStyle.secondary)
    async def set_cmi_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Use SetAwayRoleModal — this modal validates and sets the away role for the guild
        modal = SetAwayRoleModal()
        await interaction.response.send_modal(modal)

    # 8. Set CMI Prefix
    @discord.ui.button(label="Set CMI Prefix", style=discord.ButtonStyle.secondary)
    async def set_cmi_prefix(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetNicknamePrefixModal()
        await interaction.response.send_modal(modal)

    # 9. Manage Bot Perms (uses user-selection system too)
    @discord.ui.button(label="Manage Bot Perms", style=discord.ButtonStyle.secondary)
    async def manage_bot_perms(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("CMI")
        if not cog:
            return await interaction.response.send_message(
                "❌ CMI system is not available.",
                ephemeral=True,
            )

        # Start the guided selection + placeholder perms flow
        await cog.start_manage_bot_perms(interaction)

    # 10. Leadership Help
    @discord.ui.button(label="Leadership Help", style=discord.ButtonStyle.secondary)
    async def leadership_help(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = build_leadership_help_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # 11. Export CMIs to CSV
    @discord.ui.button(label="Export CMIs to CSV", style=discord.ButtonStyle.success)
    async def export_csv(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        try:
            csv_file = await generate_csv_export(interaction.guild)
            await interaction.followup.send(
                "✅ Here's your CSV export of all CMI entries:",
                file=csv_file,
                ephemeral=True
            )
        except Exception as e:
            logging.exception("Failed to generate CSV export")
            await interaction.followup.send(
                f"❌ Failed to generate CSV export: {e}",
                ephemeral=True
            )

    # 12. Daily CMI Report Settings
    @discord.ui.button(label="Daily CMI Report Settings", style=discord.ButtonStyle.success)
    async def daily_report_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            modal = DailyReportSettingsModal(self.guild_id)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logging.exception("Failed to open daily report settings modal")
            try:
                await interaction.response.send_message(
                    f"❌ Failed to open settings modal: {e}",
                    ephemeral=True
                )
            except:
                pass

    # 13. Test Daily Report Now
    @discord.ui.button(label="Test Daily Report Now", style=discord.ButtonStyle.secondary)
    async def test_daily_report(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        # Check if reports are enabled
        enabled, channel_id, report_hour = get_daily_report_settings(self.guild_id)
        
        if not enabled:
            return await interaction.followup.send(
                "❌ Daily reports are currently disabled. Enable them first in Daily CMI Report Settings.",
                ephemeral=True
            )
        
        # Determine target channel
        if channel_id:
            channel = interaction.guild.get_channel(channel_id)
        else:
            channel_id_from_settings = get_cmi_channel_id(self.guild_id)
            channel = interaction.guild.get_channel(channel_id_from_settings) if channel_id_from_settings else None

        if not channel:
            return await interaction.followup.send(
                "❌ No valid channel configured. Set a report channel or configure a CMI channel first.",
                ephemeral=True
            )

        # Get server timezone
        server_tz_name = get_server_timezone_text(self.guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        
        # Generate and send report
        try:
            report_content = await generate_daily_cmi_report(interaction.guild, server_tz)
            if report_content:
                await channel.send(report_content)
                await interaction.followup.send(
                    f"✅ Test report sent to {channel.mention}!",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    "❌ Failed to generate report (no content returned).",
                    ephemeral=True
                )
        except Exception as e:
            logging.exception("Failed to send test daily report")
            await interaction.followup.send(
                f"❌ Failed to send test report: {e}",
                ephemeral=True
            )

# ============================================================
# Section 11A‑1 — The CMI Cog (Create CMI + Manage CMI)
# ============================================================

class CMI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_views = {}  # Prevent ephemeral view GC
    
    async def cog_check(self, ctx):
        """This runs before all commands in this cog."""
        return True
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check cooldown before processing interactions, but bypass for leadership."""
        # Check if user is leadership (bypass cooldown)
        if await is_leadership(interaction):
            return True
        return True

    # Helper to build continue button view (shared by handlers)
    def _make_continue_view(self, target_member: discord.Member, for_perms: bool = False):
        class _TempButton(discord.ui.View):
            def __init__(self, target, perms=False):
                super().__init__(timeout=15)
                self.target = target
                self.perms = perms

                label_text = "Open Perms Form" if self.perms else "Open CMI Form"
                button = discord.ui.Button(
                    label=label_text,
                    style=discord.ButtonStyle.primary,
                )
                button.callback = self.open_modal
                self.add_item(button)

            async def open_modal(self, button_interaction: discord.Interaction):
                try:
                    if self.perms:
                        modal = ManageBotPermsModal(target_member=self.target)
                    else:
                        modal = CreateCMIModal(target_user=self.target)
                    await button_interaction.response.send_modal(modal)
                except Exception:
                    logging.exception("Failed to open modal from TempButton")
                    try:
                        await button_interaction.response.send_message(
                            "❌ Something went wrong opening the modal.",
                            ephemeral=True,
                        )
                    except Exception:
                        logging.exception("Failed to send error followup in TempButton.open_modal")

        return _TempButton(target_member, perms=for_perms)

    def _format_perm_roles(self, guild: discord.Guild) -> str:
        role_ids = get_bot_perm_roles(guild.id)
        if not role_ids:
            return "(none)"
        parts = []
        for rid in role_ids:
            role = guild.get_role(rid)
            parts.append(role.mention if role else f"<@&{rid}>")
        return ", ".join(parts)

    def _format_perm_users(self, guild: discord.Guild) -> str:
        user_ids = get_bot_perm_users(guild.id)
        if not user_ids:
            return "(none)"
        parts = []
        for uid in user_ids:
            member = guild.get_member(uid)
            parts.append(member.mention if member else f"<@{uid}>")
        return ", ".join(parts)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        # Auto-clean user-specific bot perms when a member leaves
        try:
            remove_bot_perm_user(member.guild.id, member.id)
        except Exception:
            logging.exception("Failed to prune bot perm user on member remove")

    # --------------------------------------------------------
    # Create CMI (from modal)
    # --------------------------------------------------------
    async def handle_create_from_modal(
        self,
        interaction: discord.Interaction,
        modal: CreateCMIModal,
    ):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        # Enforce channel restriction
        if not await enforce_cmi_channel(interaction):
            return

        target = modal.target_user or interaction.user

        leave_date = modal.leave_date.value.strip() if modal.leave_date.value else ""
        leave_time = modal.leave_time.value.strip() if modal.leave_time.value else ""
        return_date = modal.return_date.value.strip() if modal.return_date.value else ""
        return_time = modal.return_time.value.strip() if modal.return_time.value else ""
        reason = modal.reason.value or None
        tz_override = None

        # Check if user is leaving both leave fields empty (start now)
        clearing_leave = (not leave_date and not leave_time)
        
        # Check if user is leaving both return fields empty (open-ended)
        clearing_return = (not return_date and not return_time)

        # Resolve effective timezone
        effective_tz, tz_source = resolve_effective_timezone(
            interaction.guild.id,
            target.id,
            tz_override,
        )
        tz_info = ZoneInfo(effective_tz)
        
        # Handle leave date/time
        if clearing_leave:
            # Both leave fields empty = start immediately (now)
            leave_dt = datetime.now(tz_info)
        else:
            # Parse leave date/time
            ld = parse_date(leave_date, tz_info) if leave_date else None
            lt = parse_time(leave_time) if leave_time else None

            if leave_date and not ld:
                return await interaction.followup.send(
                    "❌ I couldn't understand your leave date.",
                    ephemeral=True,
                )
            if leave_time and not lt:
                return await interaction.followup.send(
                    "❌ I couldn't understand your leave time.",
                    ephemeral=True,
                )

            # Apply defaults for partial inputs
            if lt and not ld:
                ld = date.today()
            
            # If date is "today" with no time, default to NOW (immediately)
            # Otherwise, default to 00:00
            if ld and not lt:
                if leave_date and leave_date.lower() == "today":
                    leave_dt = datetime.now(tz_info)
                else:
                    lt = time(0, 0)
                    leave_dt = datetime.combine(ld, lt).replace(tzinfo=tz_info)
            elif ld and lt:
                leave_dt = datetime.combine(ld, lt).replace(tzinfo=tz_info)
            else:
                # If no leave date provided, start now
                leave_dt = datetime.now(tz_info)

        # Handle return date/time
        if clearing_return:
            # Both return fields empty = open-ended CMI
            return_dt = None
        else:
            # Parse return date/time
            rd = parse_date(return_date, tz_info) if return_date else None
            rt = parse_time(return_time) if return_time else None

            if return_date and not rd:
                return await interaction.followup.send(
                    "❌ I couldn't understand your return date.",
                    ephemeral=True,
                )
            if return_time and not rt:
                return await interaction.followup.send(
                    "❌ I couldn't understand your return time.",
                    ephemeral=True,
                )

            # Apply defaults for partial inputs
            if rt and not rd:
                rd = leave_dt.date()
            
            # If no return time provided, default to 00:00 (start of day)
            if rd and not rt:
                rt = time(0, 0)

            if rd:
                return_dt = datetime.combine(rd, rt).replace(tzinfo=tz_info)
            else:
                return_dt = None

        # Overlap detection
        has_overlap, conflict = await has_overlapping_cmi(
            interaction.guild.id,
            target.id,
            leave_dt,
            return_dt,
        )

        if has_overlap:
            conflict_leave_str = conflict["leave_dt"].astimezone(tz_info).strftime(
                "%d/%m/%Y %H:%M"
            )
            if conflict["return_dt"]:
                conflict_return_str = conflict["return_dt"].astimezone(
                    tz_info
                ).strftime("%d/%m/%Y %H:%M")
                conflict_range = f"{conflict_leave_str} → {conflict_return_str}"
            else:
                conflict_range = f"{conflict_leave_str} → Until further notice"

            conflict_reason = (
                f"Reason: {conflict['reason']}"
                if conflict["reason"]
                else "No reason provided."
            )

            return await interaction.followup.send(
                "❌ This CMI overlaps with an existing one.\n"
                f"Existing CMI (ID {conflict['id']}): {conflict_range}\n"
                f"{conflict_reason}",
                ephemeral=True,
            )

        # Timezone label
        if tz_source == "override":
            tz_label = f"Overridden Timezone: {effective_tz}"
        elif tz_source == "user":
            tz_label = f"User Timezone: {effective_tz}"
        else:
            tz_label = f"Server Timezone: {effective_tz}"

        # Insert into DB
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Determine who created this CMI
        # If target_user exists (leadership creating for someone), use interaction.user.id
        # Otherwise, user is creating for themselves
        created_by_id = interaction.user.id if modal.target_user else target.id
        
        cur.execute(
            """
            INSERT INTO cmi_entries (
                guild_id, user_id, leave_dt, return_dt, reason,
                timezone_label, created_at, created_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interaction.guild.id,
                target.id,
                leave_dt.isoformat(),
                return_dt.isoformat() if return_dt else None,
                reason,
                tz_label,
                datetime.utcnow().isoformat(),
                created_by_id,
            ),
        )
        entry_id = cur.lastrowid
        conn.commit()
        conn.close()

        # Recompute away role
        await recompute_away_role_for_user(interaction.guild, target.id)

        # Build response
        local_leave = leave_dt.astimezone(tz_info)
        leave_str = local_leave.strftime("%d/%m/%Y %H:%M")
        leave_ts = to_discord_timestamp(leave_dt)

        if return_dt:
            local_return = return_dt.astimezone(tz_info)
            return_str = local_return.strftime("%d/%m/%Y %H:%M")
            return_ts = to_discord_timestamp(return_dt)
        else:
            return_str = None
            return_ts = None

        lines = []
        lines.append(f"✅ CMI created for {target.mention}! (ID {entry_id})")
        lines.append(f"**Leave:** {leave_str} ({tz_label})")
        if leave_ts:
            lines.append(f"**Leave (localized):** {leave_ts}")

        if return_str:
            lines.append(f"**Return:** {return_str} ({tz_label})")
            if return_ts:
                lines.append(f"**Return (localized):** {return_ts}")
        else:
            lines.append("**Return:** Until further notice")

        if reason:
            lines.append(f"**Reason:** {reason}")

        # Add countdown information
        now = datetime.now(timezone.utc)
        if leave_dt > now:
            # Future CMI - show time until start
            delta = leave_dt - now
            days = delta.days
            hours, remainder = divmod(delta.seconds, 3600)
            minutes = remainder // 60
            
            time_parts = []
            if days > 0:
                time_parts.append(f"{days} day{'s' if days != 1 else ''}")
            if hours > 0:
                time_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            if minutes > 0:
                time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            
            if time_parts:
                lines.append(f"**Starts in:** {', '.join(time_parts)}")
        elif return_dt:
            # Active CMI with return date - show time until return
            delta = return_dt - now
            if delta.total_seconds() > 0:
                days = delta.days
                hours, remainder = divmod(delta.seconds, 3600)
                minutes = remainder // 60
                
                time_parts = []
                if days > 0:
                    time_parts.append(f"{days} day{'s' if days != 1 else ''}")
                if hours > 0:
                    time_parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
                if minutes > 0:
                    time_parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
                
                if time_parts:
                    lines.append(f"**Returns in:** {', '.join(time_parts)}")
        else:
            # Active open-ended CMI (no return date, already started)
            lines.append("Currently CMI")

        lines.append("")
        lines.append("_Times/dates are localized for each viewer._")

        await interaction.followup.send("\n".join(lines), ephemeral=False)

    # --------------------------------------------------------
    # Leadership selection helpers (Create/Manage/Perms guided flow)
    # --------------------------------------------------------

    async def start_create_cmi_for_others(self, interaction: discord.Interaction):
        """Leadership → Create CMI for Others → open selector."""
        await interaction.response.defer(ephemeral=True)

        view = UserSelectionView(
            guild=interaction.guild,
            purpose="create_cmi_for_others",
            requester_id=interaction.user.id,
        )

        await interaction.followup.send(
            "👤 **Select a member to create a CMI for.**\n"
            "Use the dropdown or search by name.",
            view=view,
            ephemeral=True,
        )

    async def start_manage_cmi_for_others(self, interaction: discord.Interaction):
        """Leadership → Manage CMI for Others → open selector."""
        await interaction.response.defer(ephemeral=True)

        view = UserSelectionView(
            guild=interaction.guild,
            purpose="manage_cmi_for_others",
            requester_id=interaction.user.id,
        )

        await interaction.followup.send(
            "👤 **Select a member to manage their CMIs.**\n"
            "Use the dropdown or search by name.",
            view=view,
            ephemeral=True,
        )

    async def start_manage_bot_perms(self, interaction: discord.Interaction):
        """Leadership → Manage Bot Perms → show roles/users menu."""
        embed = discord.Embed(
            title="Manage Bot Permissions",
            description="Choose to manage role-based or user-based bot leadership permissions.",
            color=discord.Color.purple(),
        )

        view = BotPermsMenuView(self)

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_role_perms_menu(self, interaction: discord.Interaction):
        """Show role permissions submenu."""
        embed = discord.Embed(
            title="Manage Role Permissions",
            description="Add or remove roles that grant bot leadership permissions.",
            color=discord.Color.blue(),
        )

        view = RolePermsMenuView(self)

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def show_user_perms_menu(self, interaction: discord.Interaction):
        """Show user permissions submenu."""
        embed = discord.Embed(
            title="Manage User Permissions",
            description="Add or remove users that have bot leadership permissions.",
            color=discord.Color.blue(),
        )

        view = UserPermsMenuView(self)

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def view_role_perms(self, interaction: discord.Interaction):
        """Display current role permissions."""
        guild = interaction.guild
        embed = discord.Embed(
            title="Current Role Permissions",
            description="Roles that grant bot leadership access:",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Permitted Roles",
            value=self._format_perm_roles(guild),
            inline=False,
        )
        embed.set_footer(text="Members with Administrator/Manage Server always have access.")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def view_user_perms(self, interaction: discord.Interaction):
        """Display current user permissions."""
        guild = interaction.guild
        embed = discord.Embed(
            title="Current User Permissions",
            description="Users with bot leadership access (excluding Admins/Manage Server):",
            color=discord.Color.green(),
        )

        # Get all members with custom permissions
        manual_user_ids = set(get_bot_perm_users(guild.id))
        perm_role_ids = set(get_bot_perm_roles(guild.id))

        # Build a list of members with custom permissions
        members_with_perms = {}
        
        # Add manually added users
        for uid in manual_user_ids:
            member = guild.get_member(uid)
            if member and not member.guild_permissions.administrator and not member.guild_permissions.manage_guild:
                if uid not in members_with_perms:
                    members_with_perms[uid] = {"member": member, "sources": []}
                members_with_perms[uid]["sources"].append("Added manually")

        # Add users with permission roles
        for member in guild.members:
            if member.bot:
                continue
            if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
                continue
            
            member_role_ids = {r.id for r in member.roles}
            matching_roles = member_role_ids & perm_role_ids
            
            if matching_roles:
                if member.id not in members_with_perms:
                    members_with_perms[member.id] = {"member": member, "sources": []}
                for role_id in matching_roles:
                    role = guild.get_role(role_id)
                    if role:
                        members_with_perms[member.id]["sources"].append(f"Role: {role.name}")

        if not members_with_perms:
            embed.add_field(
                name="Custom Permissions",
                value="(none)",
                inline=False,
            )
        else:
            lines = []
            for uid, data in sorted(members_with_perms.items(), key=lambda x: x[1]["member"].display_name.lower()):
                member = data["member"]
                sources = ", ".join(data["sources"])
                lines.append(f"{member.mention} — {sources}")
            
            embed.add_field(
                name="Custom Permissions",
                value="\n".join(lines),
                inline=False,
            )

        embed.set_footer(text="Members with Administrator/Manage Server always have access.")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def handle_add_role_perm(self, interaction: discord.Interaction, role_query: str):
        """Add a role to bot permissions from modal input."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        query = role_query.strip()

        # Try ID first
        role = None
        if query.isdigit():
            role = guild.get_role(int(query))

        # Try by name (case-insensitive)
        if not role:
            for r in guild.roles:
                if r.name.lower() == query.lower():
                    role = r
                    break

        # Try partial match
        if not role:
            matches = [r for r in guild.roles if query.lower() in r.name.lower()]
            if len(matches) == 1:
                role = matches[0]
            elif len(matches) > 1:
                return await interaction.followup.send(
                    f"❌ Multiple roles matched '{query}'. Please be more specific or use the role ID.",
                    ephemeral=True,
                )

        if not role:
            return await interaction.followup.send(
                f"❌ No role found matching '{query}'.",
                ephemeral=True,
            )

        add_bot_perm_role(guild.id, role.id)
        await interaction.followup.send(
            f"✅ Added bot leadership permissions for {role.mention}.",
            ephemeral=True,
        )

    async def handle_remove_role_perm(self, interaction: discord.Interaction, role_query: str):
        """Remove a role from bot permissions from modal input."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        query = role_query.strip()

        # Try ID first
        role = None
        if query.isdigit():
            role = guild.get_role(int(query))

        # Try by name (case-insensitive)
        if not role:
            for r in guild.roles:
                if r.name.lower() == query.lower():
                    role = r
                    break

        # Try partial match
        if not role:
            matches = [r for r in guild.roles if query.lower() in r.name.lower()]
            if len(matches) == 1:
                role = matches[0]
            elif len(matches) > 1:
                return await interaction.followup.send(
                    f"❌ Multiple roles matched '{query}'. Please be more specific or use the role ID.",
                    ephemeral=True,
                )

        if not role:
            return await interaction.followup.send(
                f"❌ No role found matching '{query}'.",
                ephemeral=True,
            )

        remove_bot_perm_role(guild.id, role.id)
        await interaction.followup.send(
            f"✅ Removed bot leadership permissions from {role.mention}.",
            ephemeral=True,
        )

    async def handle_add_user_perm(self, interaction: discord.Interaction, user_query: str):
        """Add a user to bot permissions from modal input."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        query = user_query.strip()

        # Try to find member using existing helper
        member = await self.prompt_for_member(interaction, query)
        if not member:
            return  # prompt_for_member already sent error

        if member.guild_permissions.administrator:
            return await interaction.followup.send(
                f"{member.mention} already has bot access via Administrator.",
                ephemeral=True,
            )

        add_bot_perm_user(guild.id, member.id)
        await interaction.followup.send(
            f"✅ Granted bot leadership permissions to {member.mention}.",
            ephemeral=True,
        )

    async def handle_remove_user_perm(self, interaction: discord.Interaction, user_query: str):
        """Remove a user from bot permissions from modal input."""
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        query = user_query.strip()

        # Try to find member using existing helper
        member = await self.prompt_for_member(interaction, query)
        if not member:
            return  # prompt_for_member already sent error

        if member.guild_permissions.administrator:
            return await interaction.followup.send(
                f"{member.mention} has bot access via Administrator and cannot be removed.",
                ephemeral=True,
            )

        # Check if user is manually added
        manual_user_ids = set(get_bot_perm_users(guild.id))
        if member.id not in manual_user_ids:
            # Check if they have perms via role
            perm_role_ids = set(get_bot_perm_roles(guild.id))
            member_role_ids = {r.id for r in member.roles}
            matching_roles = member_role_ids & perm_role_ids
            
            if matching_roles:
                role_names = []
                for role_id in matching_roles:
                    role = guild.get_role(role_id)
                    if role:
                        role_names.append(role.name)
                return await interaction.followup.send(
                    f"❌ Unable to remove {member.mention}'s permissions.\n"
                    f"This user has permissions through the following role(s): **{', '.join(role_names)}**.\n"
                    f"Please remove the role or edit role permissions instead.",
                    ephemeral=True,
                )
            else:
                return await interaction.followup.send(
                    f"❌ {member.mention} does not have manually added bot permissions.",
                    ephemeral=True,
                )

        remove_bot_perm_user(guild.id, member.id)
        await interaction.followup.send(
            f"✅ Removed bot leadership permissions from {member.mention}.",
            ephemeral=True,
        )

    # --------------------------------------------------------
    # Callbacks for selection flow
    # --------------------------------------------------------

    async def handle_member_selected(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        purpose: str,
    ):
        """Called when a member is chosen from the dropdown."""

        # If the interaction has already been responded to (e.g., we deferred a modal
        # submit), we cannot send another modal directly. Fall back to a button that
        # opens the modal on a fresh component interaction.
        def _make_continue_view(target_member):
            class _TempButton(discord.ui.View):
                def __init__(self, target):
                    super().__init__(timeout=15)
                    self.target = target

                    button = discord.ui.Button(
                        label="Open CMI Form",
                        style=discord.ButtonStyle.primary,
                    )
                    button.callback = self.open_modal
                    self.add_item(button)

                async def open_modal(self, button_interaction: discord.Interaction):
                    try:
                        modal = CreateCMIModal(target_user=self.target)
                        await button_interaction.response.send_modal(modal)
                    except Exception:
                        logging.exception("Failed to open CreateCMIModal from TempButton")
                        try:
                            await button_interaction.response.send_message(
                                "❌ Something went wrong opening the creation modal.",
                                ephemeral=True,
                            )
                        except Exception:
                            logging.exception("Failed to send error followup in TempButton.open_modal")

            return _TempButton(target_member)

        if purpose == "create_cmi_for_others":
            modal = CreateCMIModal(target_user=member)

            if interaction.response.is_done() or interaction.type == discord.InteractionType.modal_submit:
                try:
                    return await interaction.response.send_message(
                        "Opening CMI creation…",
                        view=_make_continue_view(member),
                        ephemeral=True,
                    )
                except discord.InteractionResponded:
                    return await interaction.followup.send(
                        "Opening CMI creation…",
                        view=_make_continue_view(member),
                        ephemeral=True,
                    )

            return await interaction.response.send_modal(modal)

        if purpose == "manage_cmi_for_others":
            await interaction.response.defer(ephemeral=True)
            return await self.show_manage_cmi_ui(interaction, member)

        if purpose == "manage_bot_perms":
            # Same restriction applies for opening modals from modal submits
            if interaction.type == discord.InteractionType.modal_submit:
                class _PermsButton(discord.ui.View):
                    def __init__(self, target):
                        super().__init__(timeout=10)
                        self.target = target

                        button = discord.ui.Button(
                            label="Open Perms Form",
                            style=discord.ButtonStyle.primary,
                        )
                        button.callback = self.open_modal
                        self.add_item(button)

                    async def open_modal(self, button_interaction: discord.Interaction):
                        try:
                            modal = ManageBotPermsModal(target_member=self.target)
                            await button_interaction.response.send_modal(modal)
                        except Exception:
                            logging.exception("Failed to open ManageBotPermsModal from TempButton")
                            try:
                                await button_interaction.response.send_message(
                                    "❌ Something went wrong opening the perms modal.",
                                    ephemeral=True,
                                )
                            except Exception:
                                logging.exception("Failed to send error followup in _PermsButton.open_modal")

                return await interaction.response.send_message(
                    "Opening permissions modal…",
                    view=_PermsButton(member),
                    ephemeral=True,
                )

            modal = ManageBotPermsModal(target_member=member)
            return await interaction.response.send_modal(modal)

        return await interaction.response.send_message(
            "❌ Unknown purpose.",
            ephemeral=True,
        )

    async def handle_user_search_submission(
        self,
        interaction: discord.Interaction,
        query: str,
        purpose: str,
    ):
        """
        Handles search modal submission → resolves member → routes.
        IMPORTANT: We DO NOT defer here, because modals require an open response.
        """

        # Try to keep the interaction open, but if something already responded,
        # fall back to followup.
        member = await self.prompt_for_member(interaction, query)
        if not member:
            return  # prompt_for_member already sent the error

        try:
            return await self.handle_member_selected(interaction, member, purpose)
        except discord.InteractionResponded:
            # If the interaction is already responded (rare), send a continue button
            # to open the modal on a fresh interaction.
            if purpose == "create_cmi_for_others":
                view = self._make_continue_view(member)
                await interaction.followup.send(
                    "Opening CMI creation…",
                    view=view,
                    ephemeral=True,
                )
            elif purpose == "manage_cmi_for_others":
                # Show manage UI for the selected member
                await self.show_manage_cmi_ui(interaction, target_member=member)
            elif purpose == "manage_bot_perms":
                view = self._make_continue_view(member)
                await interaction.followup.send(
                    "Opening permissions modal…",
                    view=view,
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "❌ Could not complete selection.",
                    ephemeral=True,
                )

    async def handle_member_selection_cancelled(
        self,
        interaction: discord.Interaction,
        purpose: str,
    ):
        """Optional hook — currently unused but kept for consistency."""
        return

    # --------------------------------------------------------
    # Matching helpers
    # --------------------------------------------------------

    async def _safe_send(self, interaction: discord.Interaction, message: str):
        """Send ephemeral message safely depending on response state."""
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def prompt_for_member(
        self,
        interaction: discord.Interaction,
        query: str,
    ) -> discord.Member | None:
        """
        Resolve a member from:
        - Exact name
        - Partial name
        - Nickname
        - Username
        - Display name
        - ID
        - Mention
        - Fuzzy match fallback
        """

        guild = interaction.guild
        if not guild:
            await self._safe_send(interaction, "❌ This can only be used in a server.")
            return None

        raw = query.strip()
        raw_lower = raw.lower()

        # ----------------------------------------------------
        # ID or mention
        # ----------------------------------------------------
        mention_match = re.match(r"<@!?(\d+)>", raw)

        if raw.isdigit():
            member = guild.get_member(int(raw))
            if member:
                return member

        if mention_match:
            member_id = int(mention_match.group(1))
            member = guild.get_member(member_id)
            if member:
                return member

        # ----------------------------------------------------
        # Build searchable list
        # ----------------------------------------------------
        members = [m for m in guild.members if not m.bot]

        def all_names(m: discord.Member):
            return {
                m.name.lower(),
                (m.display_name or "").lower(),
                (m.global_name or "").lower(),
            }

        # ----------------------------------------------------
        # Exact match
        # ----------------------------------------------------
        exact_matches = [m for m in members if raw_lower in all_names(m)]

        if len(exact_matches) == 1:
            return exact_matches[0]

        # ----------------------------------------------------
        # Partial match
        # ----------------------------------------------------
        partial_matches = [
            m for m in members
            if raw_lower in m.name.lower()
            or raw_lower in (m.display_name or "").lower()
            or raw_lower in (m.global_name or "").lower()
        ]

        if len(partial_matches) == 1:
            return partial_matches[0]

        # ----------------------------------------------------
        # Multi-match dropdown (1 < n ≤ 25)
        # ----------------------------------------------------
        if 1 < len(partial_matches) <= 25:

            # Build dropdown
            options = []
            for m in sorted(
                partial_matches,
                key=lambda x: (x.display_name or x.name).lower(),
            ):
                label = f"{m.display_name} — {m.name}"
                options.append(
                    discord.SelectOption(
                        label=label[:100],
                        value=str(m.id),
                    )
                )

            view = discord.ui.View(timeout=60)
            select = discord.ui.Select(
                placeholder="Multiple matches found — select one.",
                min_values=1,
                max_values=1,
                options=options,
            )

            async def select_callback(inter: discord.Interaction):
                if inter.user.id != interaction.user.id:
                    return await inter.response.send_message(
                        "❌ Only the original requester can use this.",
                        ephemeral=True,
                    )
                await inter.response.defer(ephemeral=True)
                view.stop()

            select.callback = select_callback
            view.add_item(select)

            # Send a followup view (interaction already deferred by caller)
            await interaction.followup.send(
                "Multiple members matched your search. Please choose one:",
                view=view,
                ephemeral=True,
            )

            timeout = await view.wait()
            if timeout:
                await interaction.followup.send(
                    "❌ Selection timed out.",
                    ephemeral=True,
                )
                return None

            chosen_id = int(select.values[0])
            return guild.get_member(chosen_id)

        # ----------------------------------------------------
        # Fuzzy match fallback
        # ----------------------------------------------------
        name_map = {m.id: (m.display_name or m.name) for m in members}
        close = get_close_matches(raw, name_map.values(), n=1, cutoff=0.6)

        if close:
            for mid, nm in name_map.items():
                if nm == close[0]:
                    return guild.get_member(mid)

        # ----------------------------------------------------
        # No matches
        # ----------------------------------------------------
        await self._safe_send(
            interaction,
            "❌ **No users found with this name, please try again.**",
        )
        return None

    # --------------------------------------------------------
    # Manage CMIs (for self or others)
    # --------------------------------------------------------
    async def show_manage_cmi_ui(
        self,
        interaction: discord.Interaction,
        target_member: discord.Member,
    ):
        if not interaction.guild:
            return await interaction.followup.send(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await enforce_cmi_channel(interaction):
            return

        guild_id = interaction.guild.id
        user_is_leadership = await is_leadership(interaction)

        if target_member.id != interaction.user.id and not user_is_leadership:
            return await interaction.followup.send(
                "❌ Only leadership can manage CMIs for other members.",
                ephemeral=True,
            )

        # Fetch all CMIs for the user
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, leave_dt, return_dt, reason, timezone_label
            FROM cmi_entries
            WHERE guild_id = ? AND user_id = ?
            ORDER BY leave_dt ASC
            """,
            (guild_id, target_member.id),
        )
        all_rows = cur.fetchall()
        conn.close()

        # Filter to active/future CMIs (exclude past CMIs where return date has passed)
        server_tz_name = get_server_timezone_text(guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        now = datetime.now(server_tz)
        rows = []
        
        for row in all_rows:
            try:
                return_dt = (
                    datetime.fromisoformat(row["return_dt"])
                    if row["return_dt"]
                    else None
                )
            except Exception:
                # If can't parse return date, include it (might be corrupted data)
                rows.append(row)
                continue
            
            # Exclude if return date is in the past (CMI has ended)
            if return_dt is not None and return_dt < now:
                continue
            
            # Include: open-ended (no return) OR future return date
            rows.append(row)

        if not rows:
            if target_member.id == interaction.user.id:
                return await interaction.followup.send(
                    "ℹ You have no active CMIs to manage. Check **My History** to see past CMIs.", ephemeral=True
                )
            else:
                return await interaction.followup.send(
                    f"ℹ {target_member.mention} has no active CMIs to manage.",
                    ephemeral=True,
                )

        embed = discord.Embed(
            title=f"CMIs for {target_member.display_name}",
            description="Use the buttons under each CMI to edit, cancel, or return early.",
            color=discord.Color.blue(),
        )

        views: list[CMIEntryView] = []

        for row in rows:
            try:
                leave_dt = datetime.fromisoformat(row["leave_dt"])
            except Exception:
                continue

            try:
                return_dt = (
                    datetime.fromisoformat(row["return_dt"])
                    if row["return_dt"]
                    else None
                )
            except Exception:
                return_dt = None

            tz_label = row["timezone_label"] or "No timezone specified"

            leave_local = leave_dt.astimezone(server_tz)
            leave_str = leave_local.strftime("%d/%m/%Y %H:%M")
            leave_ts = to_discord_timestamp(leave_dt)

            if return_dt:
                return_local = return_dt.astimezone(server_tz)
                return_str = return_local.strftime("%d/%m/%Y %H:%M")
                return_ts = to_discord_timestamp(return_dt)
            else:
                return_str = "Until further notice"
                return_ts = None

            reason = row["reason"] or "No reason provided."

            field_lines = [
                f"**Leave:** {leave_str} ({tz_label})",
            ]
            if leave_ts:
                field_lines.append(f"**Leave (localized):** {leave_ts}")

            field_lines.append(f"**Return:** {return_str}")
            if return_ts:
                field_lines.append(f"**Return (localized):** {return_ts}")

            field_lines.append(f"**Reason:** {reason}")
            field_lines.append(f"**ID:** {row['id']}")

            embed.add_field(
                name=f"CMI #{row['id']}",
                value="\n".join(field_lines),
                inline=False,
            )

            view = CMIEntryView(
                cmi_id=row["id"],
                owner_id=row["user_id"],
                guild_id=guild_id,
            )
            views.append(view)

        # Main overview embed
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Send each CMI's buttons
        for row, view in zip(rows, views):
            await interaction.followup.send(
                f"Actions for CMI #{row['id']} (owner: <@{row['user_id']}>)",
                view=view,
                ephemeral=True,
            )
# ============================================================
# Section 11A‑2 — The CMI Cog (List CMIs, Previous CMIs, My History)
# ============================================================

    # --------------------------------------------------------
    # List CMIs (current + upcoming)
    # --------------------------------------------------------
    async def show_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.followup.send(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await enforce_cmi_channel(interaction):
            return

        guild_id = interaction.guild.id

        server_tz_name = get_server_timezone_text(guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        now = datetime.now(server_tz)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, leave_dt, return_dt, reason, timezone_label
            FROM cmi_entries
            WHERE guild_id = ?
            """,
            (guild_id,),
        )
        rows = cur.fetchall()
        conn.close()

        currently_away = []
        upcoming = []

        for row in rows:
            try:
                leave_dt = datetime.fromisoformat(row["leave_dt"])
            except Exception:
                continue

            try:
                return_dt = (
                    datetime.fromisoformat(row["return_dt"])
                    if row["return_dt"]
                    else None
                )
            except Exception:
                return_dt = None

            leave_local = leave_dt.astimezone(server_tz)
            return_local = return_dt.astimezone(server_tz) if return_dt else None

            if leave_local <= now and (return_local is None or return_local >= now):
                currently_away.append((row, leave_dt, return_dt))
            elif leave_local > now:
                upcoming.append((row, leave_dt, return_dt))

        # Sort
        currently_away.sort(
            key=lambda tup: (
                tup[2].astimezone(server_tz) if tup[2] else datetime.max.replace(tzinfo=server_tz)
            )
        )
        upcoming.sort(key=lambda tup: tup[1].astimezone(server_tz))

        upcoming_display = upcoming[:50]
        upcoming_limited = len(upcoming) > 50

        lines = []
        lines.append("📘 **Currently CMI**")

        if not currently_away:
            lines.append("• Nobody is currently on CMI.")
        else:
            for row, leave_dt, return_dt in currently_away:
                user = interaction.guild.get_member(row["user_id"])
                name = user.mention if user else f"<Unknown {row['user_id']}>"

                tz_label = row["timezone_label"] or "No timezone specified"

                leave_local = leave_dt.astimezone(server_tz)
                leave_str = leave_local.strftime("%d/%m/%Y %H:%M")
                leave_ts = to_discord_timestamp(leave_dt)

                if return_dt:
                    return_local = return_dt.astimezone(server_tz)
                    return_str = return_local.strftime("%d/%m/%Y %H:%M")
                    return_ts = to_discord_timestamp(return_dt)
                    
                    # Calculate time remaining
                    time_left = return_local - now
                    total_seconds = time_left.total_seconds()
                    
                    if total_seconds < 3600:  # Less than 1 hour
                        minutes_left = int(total_seconds // 60)
                        countdown = f"Back in {minutes_left} minute{'s' if minutes_left != 1 else ''}"
                    elif total_seconds < 86400:  # Less than 24 hours
                        hours_left = int(total_seconds // 3600)
                        countdown = f"Back in {hours_left} hour{'s' if hours_left != 1 else ''}"
                    else:  # 24+ hours
                        days_left = int(total_seconds // 86400)
                        countdown = f"Back in {days_left} day{'s' if days_left != 1 else ''}"
                    
                    return_info = f"{return_str} | {countdown}"
                else:
                    return_ts = None
                    return_info = "Until further notice"

                reason = f" | Reason: {row['reason']}" if row["reason"] else ""

                lines.append(
                    f"- {name} | {leave_str} ({tz_label}) → {return_info} | ID {row['id']}{reason}"
                )
                if leave_ts:
                    lines.append(f"  • Leave (localized): {leave_ts}")
                if return_ts:
                    lines.append(f"  • Return (localized): {return_ts}")

        lines.append("")
        lines.append("📗 **Upcoming CMIs**")

        if not upcoming_display:
            lines.append("• No upcoming CMIs.")
        else:
            for row, leave_dt, return_dt in upcoming_display:
                user = interaction.guild.get_member(row["user_id"])
                name = user.mention if user else f"<Unknown {row['user_id']}>"

                tz_label = row["timezone_label"] or "No timezone specified"

                leave_local = leave_dt.astimezone(server_tz)
                leave_str = leave_local.strftime("%d/%m/%Y %H:%M")
                leave_ts = to_discord_timestamp(leave_dt)

                days_until = (leave_local.date() - now.date()).days
                start_info = f"Starts in {days_until} days"

                if return_dt:
                    return_local = return_dt.astimezone(server_tz)
                    return_str = return_local.strftime("%d/%m/%Y %H:%M")
                    return_ts = to_discord_timestamp(return_dt)
                else:
                    return_str = "Until further notice"
                    return_ts = None

                reason = f" | Reason: {row['reason']}" if row["reason"] else ""

                lines.append(
                    f"- {name} | {leave_str} ({tz_label}) → {return_str} | {start_info} | ID {row['id']}{reason}"
                )
                if leave_ts:
                    lines.append(f"  • Leave (localized): {leave_ts}")
                if return_ts:
                    lines.append(f"  • Return (localized): {return_ts}")

        if upcoming_limited:
            lines.append("\n⚠ Showing first 50 upcoming CMIs…")

        lines.append("")
        lines.append("_Times/dates are localized for each viewer._")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # --------------------------------------------------------
    # View Previous CMIs (leadership only)
    # --------------------------------------------------------
    async def show_previous_cmis(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.followup.send(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await is_leadership(interaction):
            return await interaction.followup.send(
                "❌ Only leadership can view previous CMIs.",
                ephemeral=True,
            )

        guild_id = interaction.guild.id
        server_tz_name = get_server_timezone_text(guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        now = datetime.now(server_tz)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, leave_dt, return_dt, reason, timezone_label
            FROM cmi_entries
            WHERE guild_id = ?
            """,
            (guild_id,),
        )
        rows = cur.fetchall()
        conn.close()

        past = []

        for row in rows:
            try:
                leave_dt = datetime.fromisoformat(row["leave_dt"])
            except Exception:
                continue

            try:
                return_dt = (
                    datetime.fromisoformat(row["return_dt"])
                    if row["return_dt"]
                    else None
                )
            except Exception:
                return_dt = None

            if not return_dt:
                continue

            return_local = return_dt.astimezone(server_tz)
            if return_local < now:
                past.append((row, leave_dt, return_dt))

        past.sort(
            key=lambda tup: tup[2].astimezone(server_tz),
            reverse=True,
        )

        past_display = past[:100]
        past_limited = len(past) > 100

        lines = []
        lines.append("📙 **Previous CMIs (Most Recent First)**")

        if not past_display:
            lines.append("• No previous CMIs found.")
        else:
            for row, leave_dt, return_dt in past_display:
                user = interaction.guild.get_member(row["user_id"])
                name = user.mention if user else f"<Unknown {row['user_id']}>"

                tz_label = row["timezone_label"] or "No timezone specified"

                leave_local = leave_dt.astimezone(server_tz)
                leave_str = leave_local.strftime("%d/%m/%Y %H:%M")
                leave_ts = to_discord_timestamp(leave_dt)

                return_local = return_dt.astimezone(server_tz)
                return_str = return_local.strftime("%d/%m/%Y %H:%M")
                return_ts = to_discord_timestamp(return_dt)

                reason = f" | Reason: {row['reason']}" if row["reason"] else ""

                lines.append(
                    f"- {name} | {leave_str} → {return_str} ({tz_label}) | ID {row['id']}{reason}"
                )
                if leave_ts:
                    lines.append(f"  • Leave (localized): {leave_ts}")
                if return_ts:
                    lines.append(f"  • Return (localized): {return_ts}")

        if past_limited:
            lines.append("\n⚠ Showing most recent 100 CMIs…")

        lines.append("")
        lines.append("_Times/dates are localized for each viewer._")

        # Split into chunks to avoid Discord's 2000 character limit
        full_text = "\n".join(lines)
        chunks = []
        current_chunk = ""
        
        for line in lines:
            # Check if adding this line would exceed limit (leaving room for safety)
            if len(current_chunk) + len(line) + 1 > 1900:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = line
            else:
                if current_chunk:
                    current_chunk += "\n" + line
                else:
                    current_chunk = line
        
        if current_chunk:
            chunks.append(current_chunk)
        
        # Send chunks
        if not chunks:
            await interaction.followup.send("No previous CMIs to display.", ephemeral=True)
        else:
            for i, chunk in enumerate(chunks):
                if i == 0:
                    await interaction.followup.send(chunk, ephemeral=True)
                else:
                    await interaction.followup.send(chunk, ephemeral=True)

    # --------------------------------------------------------
    # My History (user’s past CMIs)
    # --------------------------------------------------------
    async def show_my_history(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.followup.send(
                "❌ This can only be used in a server.",
                ephemeral=True,
            )

        if not await enforce_cmi_channel(interaction):
            return

        guild_id = interaction.guild.id
        user_id = interaction.user.id

        server_tz_name = get_server_timezone_text(guild_id)
        server_tz_iana = normalize_timezone_input(server_tz_name) or DEFAULT_SERVER_TZ
        server_tz = ZoneInfo(server_tz_iana)
        now = datetime.now(server_tz)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, leave_dt, return_dt, reason, timezone_label
            FROM cmi_entries
            WHERE guild_id = ? AND user_id = ?
            ORDER BY leave_dt DESC
            """,
            (guild_id, user_id),
        )
        rows = cur.fetchall()
        conn.close()

        past = []

        for row in rows:
            try:
                leave_dt = datetime.fromisoformat(row["leave_dt"])
            except Exception:
                continue

            try:
                return_dt = (
                    datetime.fromisoformat(row["return_dt"])
                    if row["return_dt"]
                    else None
                )
            except Exception:
                return_dt = None

            if return_dt:
                return_local = return_dt.astimezone(server_tz)
                if return_local < now:
                    past.append((row, leave_dt, return_dt))

        if not past:
            return await interaction.followup.send(
                "ℹ You have no previous CMIs.",
                ephemeral=True,
            )

        lines = []
        lines.append("📘 **Your Previous CMIs**")

        for row, leave_dt, return_dt in past[:50]:
            tz_label = row["timezone_label"] or "No timezone specified"

            leave_local = leave_dt.astimezone(server_tz)
            leave_str = leave_local.strftime("%d/%m/%Y %H:%M")
            leave_ts = to_discord_timestamp(leave_dt)

            return_local = return_dt.astimezone(server_tz)
            return_str = return_local.strftime("%d/%m/%Y %H:%M")
            return_ts = to_discord_timestamp(return_dt)

            reason = f" | Reason: {row['reason']}" if row["reason"] else ""

            lines.append(
                f"- {leave_str} → {return_str} ({tz_label}) | ID {row['id']}{reason}"
            )
            if leave_ts:
                lines.append(f"  • Leave (localized): {leave_ts}")
            if return_ts:
                lines.append(f"  • Return (localized): {return_ts}")

        lines.append("")
        lines.append("_Times/dates are localized for each viewer._")

        await interaction.followup.send("\n".join(lines), ephemeral=True)

# ============================================================
# ============================================================
# Section 11A‑3 — User‑Selection Helpers (Create/Manage/Perms)

    # --------------------------------------------------------
    # 1. Entry points from LeadershipToolsView
    # --------------------------------------------------------

    async def start_create_cmi_for_others(self, interaction: discord.Interaction):
        """Leadership → Create CMI for Others → open selector."""
        await interaction.response.defer(ephemeral=True)

        view = UserSelectionView(
            guild=interaction.guild,
            purpose="create_cmi_for_others",
            requester_id=interaction.user.id,
        )

        await interaction.followup.send(
            "👤 **Select a member to create a CMI for.**\n"
            "Use the dropdown or search by name.",
            view=view,
            ephemeral=True,
        )

    async def start_manage_cmi_for_others(self, interaction: discord.Interaction):
        """Leadership → Manage CMIs for Others → open selector."""
        await interaction.response.defer(ephemeral=True)

        view = UserSelectionView(
            guild=interaction.guild,
            purpose="manage_cmi_for_others",
            requester_id=interaction.user.id,
        )

        await interaction.followup.send(
            "👤 **Select a member to manage their CMIs.**\n"
            "Use the dropdown or search by name.",
            view=view,
            ephemeral=True,
        )

    # --------------------------------------------------------
    # 2. Callback from MemberDropdown (user selected)
    # --------------------------------------------------------

    async def handle_member_selected(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        purpose: str,
    ):
        """Called when a member is chosen from the dropdown."""
        logging.info(
            "handle_member_selected: member=%s id=%s purpose=%s resp_done=%s interaction_type=%s",
            member,
            member.id,
            purpose,
            interaction.response.is_done(),
            interaction.type,
        )

        if purpose == "create_cmi_for_others":
            modal = CreateCMIModal(target_user=member)

            if interaction.response.is_done() or interaction.type == discord.InteractionType.modal_submit:
                try:
                    await interaction.response.send_message(
                        "Opening CMI creation…",
                        view=self._make_continue_view(member),
                        ephemeral=True,
                    )
                    logging.info("handle_member_selected: sent continue view via response (create_cmi_for_others)")
                    return
                except discord.InteractionResponded:
                    await interaction.followup.send(
                        "Opening CMI creation…",
                        view=self._make_continue_view(member),
                        ephemeral=True,
                    )
                    logging.info("handle_member_selected: sent continue view via followup after InteractionResponded (create_cmi_for_others)")
                    return
                except Exception:
                    logging.exception("handle_member_selected: failed sending continue view (create_cmi_for_others)")
                    return

            return await interaction.response.send_modal(modal)

        if purpose == "manage_cmi_for_others":
            await interaction.response.defer(ephemeral=True)
            return await self.show_manage_cmi_ui(interaction, member)

        if purpose == "manage_bot_perms":
            modal = ManageBotPermsModal(target_member=member)

            if interaction.response.is_done() or interaction.type == discord.InteractionType.modal_submit:
                try:
                    return await interaction.response.send_message(
                        "Opening permissions modal…",
                        view=self._make_continue_view(member, for_perms=True),
                        ephemeral=True,
                    )
                except discord.InteractionResponded:
                    return await interaction.followup.send(
                        "Opening permissions modal…",
                        view=self._make_continue_view(member, for_perms=True),
                        ephemeral=True,
                    )
                except Exception:
                    logging.exception("handle_member_selected: failed sending continue view (manage_bot_perms)")
                    return

            return await interaction.response.send_modal(modal)

        return await interaction.response.send_message(
            "❌ Unknown purpose.",
            ephemeral=True,
        )

    # --------------------------------------------------------
    # 3. Callback from UserSearchModal (search submitted)
    # --------------------------------------------------------

    async def handle_user_search_submission(
        self,
        interaction: discord.Interaction,
        query: str,
        purpose: str,
    ):
        """
        Handles search modal submission → resolves member → routes.
        IMPORTANT: We DO NOT defer here, because modals require an open response.
        """

        logging.info(
            "handle_user_search_submission: query=%s purpose=%s resp_done=%s interaction_type=%s",
            query,
            purpose,
            interaction.response.is_done(),
            interaction.type,
        )

        member = await self.prompt_for_member(interaction, query)
        if not member:
            logging.info("handle_user_search_submission: prompt_for_member returned None")
            return  # prompt_for_member already sent the error

        try:
            return await self.handle_member_selected(interaction, member, purpose)
        except discord.InteractionResponded:
            logging.warning("handle_user_search_submission: InteractionResponded from handle_member_selected, sending continue button")
            try:
                if purpose == "create_cmi_for_others":
                    view = self._make_continue_view(member)
                    await interaction.followup.send(
                        "Opening CMI creation…",
                        view=view,
                        ephemeral=True,
                    )
                elif purpose == "manage_cmi_for_others":
                    # Show manage UI for the selected member
                    await self.show_manage_cmi_ui(interaction, target_member=member)
                elif purpose == "manage_bot_perms":
                    view = self._make_continue_view(member, for_perms=True)
                    await interaction.followup.send(
                        "Opening permissions modal…",
                        view=view,
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "❌ Could not complete selection.",
                        ephemeral=True,
                    )
                logging.info("handle_user_search_submission: followup continue view sent after InteractionResponded")
            except Exception:
                logging.exception("handle_user_search_submission: failed to send followup after InteractionResponded")
        except Exception:
            logging.exception("handle_user_search_submission: unexpected failure")
            try:
                await interaction.followup.send(
                    "❌ Something went wrong after searching. Please try again.",
                    ephemeral=True,
                )
            except Exception:
                logging.exception("handle_user_search_submission: followup also failed")

    # --------------------------------------------------------
    # 4. Cancel callback (from Cancel button)
    # --------------------------------------------------------

    async def handle_member_selection_cancelled(
        self,
        interaction: discord.Interaction,
        purpose: str,
    ):
        """Optional hook — currently unused but kept for consistency."""
        return

    # --------------------------------------------------------
    # 5. Core matching logic (fixed for response/followup rules)
    # --------------------------------------------------------

    async def _safe_send(self, interaction: discord.Interaction, message: str):
        """Send ephemeral message safely depending on response state."""
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    async def prompt_for_member(
        self,
        interaction: discord.Interaction,
        query: str,
    ) -> discord.Member | None:
        """
        Resolve a member from:
        - Exact name
        - Partial name
        - Nickname
        - Username
        - Display name
        - ID
        - Mention
        - Fuzzy match fallback
        """

        guild = interaction.guild
        if not guild:
            await self._safe_send(interaction, "❌ This can only be used in a server.")
            return None

        raw = query.strip()
        raw_lower = raw.lower()

        # ----------------------------------------------------
        # ID or mention
        # ----------------------------------------------------
        mention_match = re.match(r"<@!?(\d+)>", raw)

        if raw.isdigit():
            member = guild.get_member(int(raw))
            if member:
                return member

        if mention_match:
            member_id = int(mention_match.group(1))
            member = guild.get_member(member_id)
            if member:
                return member

        # ----------------------------------------------------
        # Build searchable list
        # ----------------------------------------------------
        members = [m for m in guild.members if not m.bot]

        def all_names(m: discord.Member):
            return {
                m.name.lower(),
                (m.display_name or "").lower(),
                (m.global_name or "").lower(),
            }

        # ----------------------------------------------------
        # Exact match
        # ----------------------------------------------------
        exact_matches = [m for m in members if raw_lower in all_names(m)]

        if len(exact_matches) == 1:
            return exact_matches[0]

        # ----------------------------------------------------
        # Partial match (dropdown for 1–25 results to make selection explicit)
        # ----------------------------------------------------
        partial_matches = [
            m for m in members
            if raw_lower in m.name.lower()
            or raw_lower in (m.display_name or "").lower()
            or raw_lower in (m.global_name or "").lower()
        ]

        if 1 <= len(partial_matches) <= 25:

            # Build dropdown (even for a single result so the user sees who was matched)
            options = []
            for m in sorted(
                partial_matches,
                key=lambda x: (x.display_name or x.name).lower(),
            ):
                label = f"{m.display_name} — {m.name}"
                options.append(
                    discord.SelectOption(
                        label=label[:100],
                        value=str(m.id),
                    )
                )

            view = discord.ui.View(timeout=60)
            select = discord.ui.Select(
                placeholder="Select the matching member.",
                min_values=1,
                max_values=1,
                options=options,
            )

            async def select_callback(inter: discord.Interaction):
                if inter.user.id != interaction.user.id:
                    return await inter.response.send_message(
                        "❌ Only the original requester can use this.",
                        ephemeral=True,
                    )
                await inter.response.defer(ephemeral=True)
                view.stop()

            select.callback = select_callback
            view.add_item(select)

            await self._safe_send(
                interaction,
                "Found matching member(s). Please choose one:",
            )

            await interaction.followup.send(view=view, ephemeral=True)

            timeout = await view.wait()
            if timeout:
                await interaction.followup.send(
                    "❌ Selection timed out.",
                    ephemeral=True,
                )
                return None

            chosen_id = int(select.values[0])
            return guild.get_member(chosen_id)

        # ----------------------------------------------------
        # Fuzzy match fallback
        # ----------------------------------------------------
        name_map = {m.id: (m.display_name or m.name) for m in members}
        close = get_close_matches(raw, name_map.values(), n=1, cutoff=0.6)

        if close:
            for mid, nm in name_map.items():
                if nm == close[0]:
                    return guild.get_member(mid)

        # ----------------------------------------------------
        # No matches
        # ----------------------------------------------------
        await self._safe_send(
            interaction,
            "❌ **No users found with this name, please try again.**",
        )
        return None

    # --------------------------------------------------------
    # Slash Command: /cmi and interaction routing
    # --------------------------------------------------------

    @app_commands.command(name="cmi", description="Open the CMI menu.")
    @app_commands.checks.cooldown(5, 30, key=lambda i: i.user.id)
    async def cmi_command(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        # Enforce channel restriction
        if not await enforce_cmi_channel(interaction):
            return

        is_lead = await is_leadership(interaction)
        
        # Reset cooldown for leadership users
        if is_lead:
            self.cmi_command.reset_cooldown(interaction)

        embed = build_main_menu_embed(
            guild=interaction.guild,
            user=interaction.user,
            is_leadership=is_lead,
        )

        view = MainCMIMenuView(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            is_leadership=is_lead,
        )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Routes custom_id-based interactions that aren't handled
        by built-in UI callbacks.
        """
        if not interaction.type == discord.InteractionType.component:
            return

        cid = interaction.data.get("custom_id")
        if not cid:
            return

        # Set Nickname Prefix
        if cid == "cmi_set_nick_prefix":
            if not await is_leadership(interaction):
                return await interaction.response.send_message(
                    "❌ Only leadership can change the nickname prefix.",
                    ephemeral=True,
                )

            modal = SetNicknamePrefixModal()
            return await interaction.response.send_modal(modal)


# ============================================================
# Section 11B — Slash Command `/cmi`, Interaction Routing, Cog Setup
# ============================================================

    # --------------------------------------------------------
    # Slash Command: /cmi
    # --------------------------------------------------------
    @app_commands.command(name="cmi", description="Open the CMI menu.")
    async def cmi_command(self, interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message(
                "❌ This command can only be used in a server.",
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        # Enforce channel restriction
        if not await enforce_cmi_channel(interaction):
            return

        is_lead = await is_leadership(interaction)

        embed = build_main_menu_embed(
            guild=interaction.guild,
            user=interaction.user,
            is_leadership=is_lead,
        )

        view = MainCMIMenuView(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            is_leadership=is_lead,
        )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    # --------------------------------------------------------
    # Interaction Routing (buttons with custom_id)
    # --------------------------------------------------------
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """
        Routes custom_id-based interactions that aren't handled
        by built-in UI callbacks.
        """
        if not interaction.type == discord.InteractionType.component:
            return

        cid = interaction.data.get("custom_id")
        if not cid:
            return

        # Set Nickname Prefix
        if cid == "cmi_set_nick_prefix":
            if not await is_leadership(interaction):
                return await interaction.response.send_message(
                    "❌ Only leadership can change the nickname prefix.",
                    ephemeral=True,
                )

            modal = SetNicknamePrefixModal()
            return await interaction.response.send_modal(modal)

    # --------------------------------------------------------
    # Cog Setup
    # --------------------------------------------------------
async def setup(bot: commands.Bot):
    await bot.add_cog(CMI(bot))


# ============================================================
# Section 11C — Error Handlers
# ============================================================

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Handle application command errors."""
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"⏱️ **Slow down!** You can use this command again in {error.retry_after:.1f} seconds.\n"
            "This helps prevent spam and keeps the bot running smoothly.",
            ephemeral=True
        )
    else:
        # Log other errors
        logging.error(f"Command error: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ An error occurred while processing your command.",
                ephemeral=True
            )

# ============================================================
# Section 11D — Health Check HTTP Server
# ============================================================

class HealthCheckHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler for health checks (UptimeRobot monitoring)"""
    
    def do_GET(self):
        if self.path == '/health':
            # Check if bot is connected
            if bot.is_ready():
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {
                    "status": "ok",
                    "bot_connected": True,
                    "bot_user": str(bot.user) if bot.user else "Unknown"
                }
                self.wfile.write(str(response).encode())
            else:
                self.send_response(503)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {"status": "error", "bot_connected": False}
                self.wfile.write(str(response).encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        # Suppress default HTTP logging to avoid cluttering logs
        pass


def start_health_check_server():
    """Start health check HTTP server on port 8080"""
    try:
        server = HTTPServer(('0.0.0.0', 8080), HealthCheckHandler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logging.info("Health check server started on port 8080")
    except Exception as e:
        logging.error(f"Failed to start health check server: {e}")


# ============================================================
# Section 11D — Graceful Shutdown Handler
# ============================================================

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully"""
    logging.info(f"Received signal {sig}, shutting down gracefully...")
    
    # Stop background tasks
    try:
        away_role_sync_task.cancel()
        cleanup_old_cmi_task.cancel()
        logging.info("Background tasks stopped")
    except Exception as e:
        logging.error(f"Error stopping tasks: {e}")
    
    # Close bot connection
    asyncio.create_task(bot.close())
    logging.info("Bot connection closed")
    
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
signal.signal(signal.SIGTERM, signal_handler)  # systemctl stop


# ============================================================
# Section 12 — Bot Startup & Final Assembly
# ============================================================

async def main():
    # Initialize database
    init_db()
    
    async with bot:
        # Load the CMI Cog
        await bot.add_cog(CMI(bot))

        # Start the bot
        await bot.start(TOKEN)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    # Start health check server
    start_health_check_server()

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    # Start periodic away-role sync
    try:
        away_role_sync_task.start()
        print("Away-role sync task started.")
    except RuntimeError:
        pass

    # Start periodic cleanup of old CMI entries
    try:
        cleanup_old_cmi_task.start()
        print("Old CMI cleanup task started (runs daily).")
    except RuntimeError:
        pass

    # Start daily CMI report task
    try:
        daily_report_task.start()
        print("Daily CMI report task started (checks hourly).")
    except RuntimeError:
        pass


# ============================================================
# Run the Bot
# ============================================================
if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

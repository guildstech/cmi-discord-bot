# Copilot / AI Agent Instructions for CMI-disc-bot

Summary
- Single-purpose Discord bot that manages "CMI" (absence) entries backed by a local SQLite DB (`cmi.db`).
- Primary implementation lives in `bot.py` (large, single-file implementation). `cogs/` exists but is currently empty.
- Use `python bot.py` to run locally (see "Run & Dev" below).

What to know (big picture)
- `bot.py` contains the full domain logic: timezone helpers, DB schema & helpers, UI components (modals, views), the `CMI` Cog, and startup code.
- SQLite schema is created at startup by `init_db()`; DB path is `DB_PATH = "cmi.db"` (file created in repo root).
- Timezone handling: `normalize_timezone_input`, `resolve_effective_timezone`, `TIMEZONE_ALIASES` provide primary logic. Time strings are parsed by `parse_date` and `parse_time`.
- User-facing interactions use Discord modals and components extensively. Modals have special rules: they require an open response (do NOT call `response.defer()` before sending a modal).
- Interaction message flow pattern: prefer `interaction.response.send_message` when `response` is not done; otherwise use `interaction.followup.send`. The helper `_safe_send` encapsulates this pattern in the code.
- Leadership checks use `is_leadership(interaction)` (administrator or manage_guild).
- Periodic background work: `away_role_sync_task` runs every 5 minutes to reapply away roles and nick prefixes.

Project-specific conventions & patterns
- Ephemeral replies are used for almost all UI/flow responses (`ephemeral=True`) to keep the UI private.
- Overlap detection: `has_overlapping_cmi()` is used before inserts/edits to prevent conflicting CMIs.
- Database access: `get_db_connection()` returns `sqlite3.connect(DB_PATH, check_same_thread=False)` and `row_factory = sqlite3.Row`. Expect code to open/close connections frequently (simple, not pooled).
- Time formatting: many display strings use `%d/%m/%Y %H:%M`. Discord localization timestamps are generated using `to_discord_timestamp(dt)` which returns `<t:..:f>`.
- User resolution: `resolve_users_advanced` and `prompt_for_member` implement a consistent multi-step lookup order (ID, mention, exact, case-insensitive, partial, fuzzy fallback). Use these helpers when adding functionality that needs to resolve guild members.
- UI design: For multi-match situations, code favors dropdown selection (up to 25 matches) and falls back to fuzzy match if needed.

Key files & code pointers (examples)
- Main: `bot.py` — contains the DB schema, timezone helpers, modal classes (`CreateCMIModal`, `SetUserTimezoneModal`, etc.), UI `View`s, and the `CMI` Cog with `/cmi` command registration.
- DB schema creation: `init_db()` near top of `bot.py` — update schema here if adding fields.
- Time parsing: `parse_date` and `parse_time` — add new formats here if necessary.
- Permission check example: `if not await is_leadership(interaction): return await interaction.response.send_message("❌ Only leadership can...", ephemeral=True)`
- Modal rules example comment: "IMPORTANT: We DO NOT defer here, because modals require an open response." (see `handle_user_search_submission`)

Running & debugging
- Run locally: `python bot.py` (this will create `cmi.db` if absent). The bot syncs slash commands on startup in `on_ready()`.
- No test suite present — `test.py` just prints a message. Before making changes run `python bot.py` in a dev server and exercise the `/cmi` flow.
- Logging: the bot prints to stdout on `on_ready()` and periodic task start messages. Add logging or use a debugger for deeper inspection.

Notable issues & TODOs for contributors (so agents don't miss them)
- Sensitive data: A bot token is hard-coded in `bot.py` as `TOKEN = "..."`. Replace with environment variable usage (e.g., `os.environ['DISCORD_TOKEN']`) before committing or sharing tokens.
- Code organization: The main logic is a single large file. Consider moving `CMI` Cog and UI components into `cogs/cmi.py` and `cogs/views.py` (the `cogs/` directory exists but is empty), this will make future changes easier to reason about.
- Duplicate/merged code: There appear to be repeated `class CMI` sections (refactor may be needed). Verify there are no unintended duplicated definitions when making edits.
- No tests: There is no automated test coverage — be conservative with changes and test manually in a development server.

How AI agents should behave when editing
- Keep changes minimal and well-scoped when touching `bot.py`—this file contains many tightly coupled behaviors.
- Preserve existing user-visible strings and button labels unless intentionally updating UX; many server owners rely on the exact text used by UI components.
- When editing asynchronous flows involving modals and `interaction.response`, follow the code's established rules (defer vs direct response) — see `handle_user_search_submission` and `_safe_send`.
- Avoid committing bot tokens; if you need to run the bot in CI, add instructions to set `DISCORD_TOKEN` env var and read it from code.

Suggested quick PR checklist for agents
- Run the bot locally and exercise flows you changed (`/cmi`, create/manage, set timezone/role/prefix).
- Verify DB migration (if schema changes) by updating `init_db()` and ensuring `init_db()` is idempotent.
- Ensure any UI changes are accessible with keyboards and support ephemeral flows.

If anything is unclear or you'd like the instructions tuned for a particular automation style (e.g., focus on refactoring vs feature development), tell me which sections to expand or examples you'd like added.🙏

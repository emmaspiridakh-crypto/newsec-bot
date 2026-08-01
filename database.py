import json
import os
import time

import libsql_client

CONFIG_DEFAULTS = {
    "alt_age_days":       "30",
    "alt_action":         "kick",
    "spam_threshold":     "5",
    "spam_window_secs":   "5",
    "spam_timeout_mins":  "10",
    "link_timeout_mins":  "60",
    "mass_action_limit":  "3",
    "mass_action_window": "10",
    "role_action_limit":  "3",
    "role_action_window": "10",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS server_owners (
    guild_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS whitelist_users (
    guild_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS whitelist_bots (
    guild_id TEXT NOT NULL,
    bot_id   TEXT NOT NULL,
    PRIMARY KEY (guild_id, bot_id)
);
CREATE TABLE IF NOT EXISTS whitelist_channels (
    guild_id   TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    PRIMARY KEY (guild_id, channel_id)
);
CREATE TABLE IF NOT EXISTS whitelist_roles (
    guild_id TEXT NOT NULL,
    role_id  TEXT NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);
CREATE TABLE IF NOT EXISTS disabled_modules (
    guild_id    TEXT NOT NULL,
    module_name TEXT NOT NULL,
    PRIMARY KEY (guild_id, module_name)
);
CREATE TABLE IF NOT EXISTS config (
    guild_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT,
    PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS settings (
    guild_id TEXT NOT NULL,
    key      TEXT NOT NULL,
    value    TEXT,
    PRIMARY KEY (guild_id, key)
);
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id  TEXT    NOT NULL,
    type      TEXT    NOT NULL,
    data      TEXT,
    timestamp INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_guild ON events (guild_id);
"""


class Database:
    """
    Turso (hosted libSQL) backed storage.

    Set TURSO_DATABASE_URL + TURSO_AUTH_TOKEN in the environment to point at
    a Turso database — settings then survive redeploys/restarts on Render.
    If those are not set, falls back to a local file (file:security.db) so
    local development still works without a Turso account.
    """

    _client = None

    # ── Connection ─────────────────────────────────────────
    @classmethod
    def _make_client(cls):
        url        = os.getenv("TURSO_DATABASE_URL")
        auth_token = os.getenv("TURSO_AUTH_TOKEN")

        if url:
            return libsql_client.create_client(url=url, auth_token=auth_token)

        print("[Database] TURSO_DATABASE_URL not set — falling back to local file:security.db "
              "(settings will NOT persist across redeploys).")
        return libsql_client.create_client(url="file:security.db")

    @classmethod
    async def init(cls):
        cls._client = cls._make_client()
        for statement in filter(None, (s.strip() for s in SCHEMA.split(";"))):
            await cls._client.execute(statement)

    @classmethod
    async def close(cls):
        if cls._client:
            await cls._client.close()

    @classmethod
    async def _execute(cls, sql: str, args: tuple = ()):
        return await cls._client.execute(sql, args)

    # ── Server Owners ─────────────────────────────────────
    @classmethod
    async def add_server_owner(cls, guild_id: str, user_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO server_owners VALUES (?, ?)", (guild_id, user_id)
        )

    @classmethod
    async def remove_server_owner(cls, guild_id: str, user_id: str):
        await cls._execute(
            "DELETE FROM server_owners WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )

    @classmethod
    async def is_server_owner(cls, guild_id: str, user_id: str, installer_id: str = None) -> bool:
        if installer_id and str(user_id) == str(installer_id):
            return True
        rs = await cls._execute(
            "SELECT 1 FROM server_owners WHERE guild_id=? AND user_id=?",
            (guild_id, str(user_id))
        )
        return len(rs.rows) > 0

    @classmethod
    async def get_server_owners(cls, guild_id: str) -> list:
        rs = await cls._execute(
            "SELECT user_id FROM server_owners WHERE guild_id=?", (guild_id,)
        )
        return [r[0] for r in rs.rows]

    # ── Whitelist Users ───────────────────────────────────
    @classmethod
    async def add_whitelist_user(cls, guild_id: str, user_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_users VALUES (?, ?)", (guild_id, user_id)
        )

    @classmethod
    async def remove_whitelist_user(cls, guild_id: str, user_id: str):
        await cls._execute(
            "DELETE FROM whitelist_users WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )

    @classmethod
    async def is_whitelist_user(cls, guild_id: str, user_id: str) -> bool:
        rs = await cls._execute(
            "SELECT 1 FROM whitelist_users WHERE guild_id=? AND user_id=?",
            (guild_id, str(user_id))
        )
        return len(rs.rows) > 0

    @classmethod
    async def get_whitelist_users(cls, guild_id: str) -> list:
        rs = await cls._execute(
            "SELECT user_id FROM whitelist_users WHERE guild_id=?", (guild_id,)
        )
        return [r[0] for r in rs.rows]

    # ── Whitelist Bots ────────────────────────────────────
    @classmethod
    async def add_whitelist_bot(cls, guild_id: str, bot_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_bots VALUES (?, ?)", (guild_id, bot_id)
        )

    @classmethod
    async def remove_whitelist_bot(cls, guild_id: str, bot_id: str):
        await cls._execute(
            "DELETE FROM whitelist_bots WHERE guild_id=? AND bot_id=?", (guild_id, bot_id)
        )

    @classmethod
    async def is_whitelist_bot(cls, guild_id: str, bot_id: str) -> bool:
        rs = await cls._execute(
            "SELECT 1 FROM whitelist_bots WHERE guild_id=? AND bot_id=?",
            (guild_id, str(bot_id))
        )
        return len(rs.rows) > 0

    @classmethod
    async def get_whitelist_bots(cls, guild_id: str) -> list:
        rs = await cls._execute(
            "SELECT bot_id FROM whitelist_bots WHERE guild_id=?", (guild_id,)
        )
        return [r[0] for r in rs.rows]

    # ── Whitelist Channels ────────────────────────────────
    @classmethod
    async def add_whitelist_channel(cls, guild_id: str, channel_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_channels VALUES (?, ?)", (guild_id, channel_id)
        )

    @classmethod
    async def remove_whitelist_channel(cls, guild_id: str, channel_id: str):
        await cls._execute(
            "DELETE FROM whitelist_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, channel_id)
        )

    @classmethod
    async def is_whitelist_channel(cls, guild_id: str, channel_id: str) -> bool:
        rs = await cls._execute(
            "SELECT 1 FROM whitelist_channels WHERE guild_id=? AND channel_id=?",
            (guild_id, str(channel_id))
        )
        return len(rs.rows) > 0

    @classmethod
    async def get_whitelist_channels(cls, guild_id: str) -> list:
        rs = await cls._execute(
            "SELECT channel_id FROM whitelist_channels WHERE guild_id=?", (guild_id,)
        )
        return [r[0] for r in rs.rows]

    # ── Whitelist Roles ───────────────────────────────────
    @classmethod
    async def add_whitelist_role(cls, guild_id: str, role_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_roles VALUES (?, ?)", (guild_id, role_id)
        )

    @classmethod
    async def remove_whitelist_role(cls, guild_id: str, role_id: str):
        await cls._execute(
            "DELETE FROM whitelist_roles WHERE guild_id=? AND role_id=?", (guild_id, role_id)
        )

    @classmethod
    async def is_whitelist_role(cls, guild_id: str, role_id: str) -> bool:
        rs = await cls._execute(
            "SELECT 1 FROM whitelist_roles WHERE guild_id=? AND role_id=?",
            (guild_id, str(role_id))
        )
        return len(rs.rows) > 0

    @classmethod
    async def has_whitelist_role(cls, guild_id: str, role_ids: list) -> bool:
        if not role_ids:
            return False
        placeholders = ",".join("?" for _ in role_ids)
        rs = await cls._execute(
            f"SELECT 1 FROM whitelist_roles WHERE guild_id=? AND role_id IN ({placeholders}) LIMIT 1",
            (guild_id, *[str(r) for r in role_ids])
        )
        return len(rs.rows) > 0

    @classmethod
    async def get_whitelist_roles(cls, guild_id: str) -> list:
        rs = await cls._execute(
            "SELECT role_id FROM whitelist_roles WHERE guild_id=?", (guild_id,)
        )
        return [r[0] for r in rs.rows]

    # ── Modules ───────────────────────────────────────────
    @classmethod
    async def disable_module(cls, guild_id: str, name: str):
        await cls._execute(
            "INSERT OR IGNORE INTO disabled_modules VALUES (?, ?)", (guild_id, name)
        )

    @classmethod
    async def enable_module(cls, guild_id: str, name: str):
        await cls._execute(
            "DELETE FROM disabled_modules WHERE guild_id=? AND module_name=?", (guild_id, name)
        )

    @classmethod
    async def is_module_enabled(cls, guild_id: str, name: str) -> bool:
        rs = await cls._execute(
            "SELECT 1 FROM disabled_modules WHERE guild_id=? AND module_name=?",
            (guild_id, name)
        )
        return len(rs.rows) == 0

    @classmethod
    async def get_disabled_modules(cls, guild_id: str) -> list:
        rs = await cls._execute(
            "SELECT module_name FROM disabled_modules WHERE guild_id=?", (guild_id,)
        )
        return [r[0] for r in rs.rows]

    # ── Config ────────────────────────────────────────────
    @classmethod
    async def get_config(cls, guild_id: str, key: str, default=None) -> str:
        rs = await cls._execute(
            "SELECT value FROM config WHERE guild_id=? AND key=?", (guild_id, key)
        )
        if rs.rows:
            return rs.rows[0][0]
        return default or CONFIG_DEFAULTS.get(key)

    @classmethod
    async def set_config(cls, guild_id: str, key: str, value: str):
        await cls._execute(
            "INSERT OR REPLACE INTO config (guild_id, key, value) VALUES (?, ?, ?)",
            (guild_id, key, value)
        )

    @classmethod
    async def get_all_config(cls, guild_id: str) -> dict:
        rs = await cls._execute(
            "SELECT key, value FROM config WHERE guild_id=?", (guild_id,)
        )
        stored = {r[0]: r[1] for r in rs.rows}
        result = dict(CONFIG_DEFAULTS)
        result.update(stored)
        return result

    # ── Settings ──────────────────────────────────────────
    @classmethod
    async def get_setting(cls, guild_id: str, key: str, default=None) -> str:
        rs = await cls._execute(
            "SELECT value FROM settings WHERE guild_id=? AND key=?", (guild_id, key)
        )
        return rs.rows[0][0] if rs.rows else default

    @classmethod
    async def set_setting(cls, guild_id: str, key: str, value: str):
        await cls._execute(
            "INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)",
            (guild_id, key, value)
        )

    # ── Events ────────────────────────────────────────────
    @classmethod
    async def log_event(cls, guild_id: str, event_type: str, data: dict):
        await cls._execute(
            "INSERT INTO events (guild_id, type, data, timestamp) VALUES (?, ?, ?, ?)",
            (guild_id, event_type, json.dumps(data), int(time.time() * 1000))
        )
        await cls._execute("""
            DELETE FROM events WHERE guild_id=? AND id NOT IN (
                SELECT id FROM events WHERE guild_id=? ORDER BY id DESC LIMIT 500
            )
        """, (guild_id, guild_id))

    @classmethod
    async def get_recent_events(cls, guild_id: str, limit: int = 10) -> list:
        rs = await cls._execute(
            "SELECT type, data, timestamp FROM events WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (guild_id, limit)
        )
        return [
            {"type": r[0], "data": json.loads(r[1]) if r[1] else {}, "timestamp": r[2]}
            for r in rs.rows
        ]

import json
import os
import time

import libsql_client

from utils.cache import TTLCache, MISSING

CONFIG_DEFAULTS = {
    "alt_age_days":         "30",
    "alt_action":           "kick",      # kick / log / quarantine
    "alt_score_threshold":  "50",
    "spam_threshold":       "5",
    "spam_window_secs":     "5",
    "spam_timeout_mins":    "10",
    "link_timeout_mins":    "60",
    "mass_action_limit":    "3",
    "mass_action_window":   "10",
    "role_action_limit":    "3",
    "role_action_window":   "10",
    "nuke_timeout_hours":   "168",
    "chan_action_limit":    "4",
    "chan_action_window":   "10",
    "emoji_action_limit":   "3",
    "emoji_action_window":  "10",
    "invite_action_limit":  "5",
    "invite_action_window": "10",
    "purge_limit":          "3",
    "purge_window":         "30",
    "everyone_limit":       "3",
    "everyone_window":      "60",
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
CREATE TABLE IF NOT EXISTS trusted_admins (
    guild_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
CREATE TABLE IF NOT EXISTS lockdown_state (
    guild_id TEXT PRIMARY KEY,
    kind     TEXT NOT NULL,
    reason   TEXT,
    actor_id TEXT,
    channels TEXT NOT NULL DEFAULT '{}',
    roles    TEXT NOT NULL DEFAULT '{}',
    started  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS backups (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id TEXT NOT NULL,
    kind     TEXT NOT NULL,
    label    TEXT,
    created  INTEGER NOT NULL,
    data     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backups_guild ON backups (guild_id, id);
CREATE TABLE IF NOT EXISTS quarantined (
    guild_id TEXT NOT NULL,
    user_id  TEXT NOT NULL,
    roles    TEXT NOT NULL DEFAULT '[]',
    reason   TEXT,
    created  INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);
"""

EVENT_RETENTION = 1000


class Database:
    """
    Turso (hosted libSQL) backed storage.

    Κάθε πίνακας έχει guild_id στο primary key, άρα οι servers δεν μπερδεύονται
    ποτέ μεταξύ τους. Τα hot-path reads (config, modules, whitelist, owners)
    σερβίρονται από in-memory cache ανά server και ακυρώνονται σε κάθε write.

    Set TURSO_DATABASE_URL + TURSO_AUTH_TOKEN in the environment to point at
    a Turso database. Αλλιώς πέφτει σε τοπικό αρχείο file:security.db.
    """

    _client = None
    _cache  = TTLCache(600)
    _event_counter: dict = {}
    bot = None   # ορίζεται από το main.py — χρειάζεται για τον πραγματικό owner του server

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

    # ── Cache helpers ─────────────────────────────────────
    @classmethod
    async def _cached(cls, gid, ns: str, loader):
        key = (str(gid), ns)
        val = cls._cache.get(key)
        if val is MISSING:
            val = await loader()
            cls._cache.set(key, val)
        return val

    @classmethod
    def _invalidate(cls, gid, *namespaces: str):
        for ns in namespaces:
            cls._cache.delete((str(gid), ns))

    @classmethod
    def invalidate_guild(cls, gid):
        cls._cache.delete_guild(str(gid))

    @classmethod
    async def _id_set(cls, gid, table: str, col: str) -> frozenset:
        gid = str(gid)

        async def load():
            rs = await cls._execute(f"SELECT {col} FROM {table} WHERE guild_id=?", (gid,))
            return frozenset(str(r[0]) for r in rs.rows)
        return await cls._cached(gid, table, load)

    # ── Server Owners ─────────────────────────────────────
    @classmethod
    async def add_server_owner(cls, guild_id: str, user_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO server_owners VALUES (?, ?)", (str(guild_id), str(user_id))
        )
        cls._invalidate(guild_id, "server_owners")

    @classmethod
    async def remove_server_owner(cls, guild_id: str, user_id: str):
        await cls._execute(
            "DELETE FROM server_owners WHERE guild_id=? AND user_id=?", (str(guild_id), str(user_id))
        )
        cls._invalidate(guild_id, "server_owners")

    @classmethod
    async def is_server_owner(cls, guild_id: str, user_id: str, installer_id: str = None) -> bool:
        """True για: τον installer, τον πραγματικό owner του Discord server,
        και όσους προστέθηκαν με /addserverowner."""
        user_id = str(user_id)
        if installer_id and user_id == str(installer_id):
            return True
        if cls.bot is not None:
            try:
                guild = cls.bot.get_guild(int(guild_id))
            except (TypeError, ValueError):
                guild = None
            if guild is not None and str(guild.owner_id) == user_id:
                return True
        return user_id in await cls._id_set(guild_id, "server_owners", "user_id")

    @classmethod
    async def get_server_owners(cls, guild_id: str) -> list:
        return sorted(await cls._id_set(guild_id, "server_owners", "user_id"))

    # ── Trusted Admins (εξαιρούνται από το Anti-Nuke) ─────
    @classmethod
    async def add_trusted(cls, guild_id: str, user_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO trusted_admins VALUES (?, ?)", (str(guild_id), str(user_id))
        )
        cls._invalidate(guild_id, "trusted_admins")

    @classmethod
    async def remove_trusted(cls, guild_id: str, user_id: str):
        await cls._execute(
            "DELETE FROM trusted_admins WHERE guild_id=? AND user_id=?", (str(guild_id), str(user_id))
        )
        cls._invalidate(guild_id, "trusted_admins")

    @classmethod
    async def is_trusted(cls, guild_id: str, user_id: str) -> bool:
        return str(user_id) in await cls._id_set(guild_id, "trusted_admins", "user_id")

    @classmethod
    async def get_trusted(cls, guild_id: str) -> list:
        return sorted(await cls._id_set(guild_id, "trusted_admins", "user_id"))

    # ── Whitelist Users ───────────────────────────────────
    @classmethod
    async def add_whitelist_user(cls, guild_id: str, user_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_users VALUES (?, ?)", (str(guild_id), str(user_id))
        )
        cls._invalidate(guild_id, "whitelist_users")

    @classmethod
    async def remove_whitelist_user(cls, guild_id: str, user_id: str):
        await cls._execute(
            "DELETE FROM whitelist_users WHERE guild_id=? AND user_id=?", (str(guild_id), str(user_id))
        )
        cls._invalidate(guild_id, "whitelist_users")

    @classmethod
    async def is_whitelist_user(cls, guild_id: str, user_id: str) -> bool:
        return str(user_id) in await cls._id_set(guild_id, "whitelist_users", "user_id")

    @classmethod
    async def get_whitelist_users(cls, guild_id: str) -> list:
        return sorted(await cls._id_set(guild_id, "whitelist_users", "user_id"))

    # ── Whitelist Bots ────────────────────────────────────
    @classmethod
    async def add_whitelist_bot(cls, guild_id: str, bot_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_bots VALUES (?, ?)", (str(guild_id), str(bot_id))
        )
        cls._invalidate(guild_id, "whitelist_bots")

    @classmethod
    async def remove_whitelist_bot(cls, guild_id: str, bot_id: str):
        await cls._execute(
            "DELETE FROM whitelist_bots WHERE guild_id=? AND bot_id=?", (str(guild_id), str(bot_id))
        )
        cls._invalidate(guild_id, "whitelist_bots")

    @classmethod
    async def is_whitelist_bot(cls, guild_id: str, bot_id: str) -> bool:
        return str(bot_id) in await cls._id_set(guild_id, "whitelist_bots", "bot_id")

    @classmethod
    async def get_whitelist_bots(cls, guild_id: str) -> list:
        return sorted(await cls._id_set(guild_id, "whitelist_bots", "bot_id"))

    # ── Whitelist Channels ────────────────────────────────
    @classmethod
    async def add_whitelist_channel(cls, guild_id: str, channel_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_channels VALUES (?, ?)", (str(guild_id), str(channel_id))
        )
        cls._invalidate(guild_id, "whitelist_channels")

    @classmethod
    async def remove_whitelist_channel(cls, guild_id: str, channel_id: str):
        await cls._execute(
            "DELETE FROM whitelist_channels WHERE guild_id=? AND channel_id=?",
            (str(guild_id), str(channel_id))
        )
        cls._invalidate(guild_id, "whitelist_channels")

    @classmethod
    async def is_whitelist_channel(cls, guild_id: str, channel_id: str) -> bool:
        return str(channel_id) in await cls._id_set(guild_id, "whitelist_channels", "channel_id")

    @classmethod
    async def get_whitelist_channels(cls, guild_id: str) -> list:
        return sorted(await cls._id_set(guild_id, "whitelist_channels", "channel_id"))

    # ── Whitelist Roles ───────────────────────────────────
    @classmethod
    async def add_whitelist_role(cls, guild_id: str, role_id: str):
        await cls._execute(
            "INSERT OR IGNORE INTO whitelist_roles VALUES (?, ?)", (str(guild_id), str(role_id))
        )
        cls._invalidate(guild_id, "whitelist_roles")

    @classmethod
    async def remove_whitelist_role(cls, guild_id: str, role_id: str):
        await cls._execute(
            "DELETE FROM whitelist_roles WHERE guild_id=? AND role_id=?", (str(guild_id), str(role_id))
        )
        cls._invalidate(guild_id, "whitelist_roles")

    @classmethod
    async def is_whitelist_role(cls, guild_id: str, role_id: str) -> bool:
        return str(role_id) in await cls._id_set(guild_id, "whitelist_roles", "role_id")

    @classmethod
    async def has_whitelist_role(cls, guild_id: str, role_ids: list) -> bool:
        if not role_ids:
            return False
        wl = await cls._id_set(guild_id, "whitelist_roles", "role_id")
        return any(str(r) in wl for r in role_ids)

    @classmethod
    async def get_whitelist_roles(cls, guild_id: str) -> list:
        return sorted(await cls._id_set(guild_id, "whitelist_roles", "role_id"))

    # ── Modules ───────────────────────────────────────────
    @classmethod
    async def disable_module(cls, guild_id: str, name: str):
        await cls._execute(
            "INSERT OR IGNORE INTO disabled_modules VALUES (?, ?)", (str(guild_id), name)
        )
        cls._invalidate(guild_id, "disabled_modules")

    @classmethod
    async def enable_module(cls, guild_id: str, name: str):
        await cls._execute(
            "DELETE FROM disabled_modules WHERE guild_id=? AND module_name=?", (str(guild_id), name)
        )
        cls._invalidate(guild_id, "disabled_modules")

    @classmethod
    async def is_module_enabled(cls, guild_id: str, name: str) -> bool:
        return name not in await cls._id_set(guild_id, "disabled_modules", "module_name")

    @classmethod
    async def get_disabled_modules(cls, guild_id: str) -> list:
        return sorted(await cls._id_set(guild_id, "disabled_modules", "module_name"))

    # ── Config ────────────────────────────────────────────
    @classmethod
    async def _stored_config(cls, guild_id) -> dict:
        gid = str(guild_id)

        async def load():
            rs = await cls._execute("SELECT key, value FROM config WHERE guild_id=?", (gid,))
            return {r[0]: r[1] for r in rs.rows}
        return await cls._cached(gid, "config", load)

    @classmethod
    async def get_config(cls, guild_id: str, key: str, default=None) -> str:
        stored = await cls._stored_config(guild_id)
        if key in stored:
            return stored[key]
        return default or CONFIG_DEFAULTS.get(key)

    @classmethod
    async def set_config(cls, guild_id: str, key: str, value: str):
        await cls._execute(
            "INSERT OR REPLACE INTO config (guild_id, key, value) VALUES (?, ?, ?)",
            (str(guild_id), key, value)
        )
        cls._invalidate(guild_id, "config")

    @classmethod
    async def get_all_config(cls, guild_id: str) -> dict:
        result = dict(CONFIG_DEFAULTS)
        result.update(await cls._stored_config(guild_id))
        return result

    @classmethod
    async def get_int(cls, guild_id: str, key: str, default: int = 0) -> int:
        try:
            return int(await cls.get_config(guild_id, key))
        except (TypeError, ValueError):
            return default

    # ── Settings ──────────────────────────────────────────
    @classmethod
    async def _stored_settings(cls, guild_id) -> dict:
        gid = str(guild_id)

        async def load():
            rs = await cls._execute("SELECT key, value FROM settings WHERE guild_id=?", (gid,))
            return {r[0]: r[1] for r in rs.rows}
        return await cls._cached(gid, "settings", load)

    @classmethod
    async def get_setting(cls, guild_id: str, key: str, default=None) -> str:
        return (await cls._stored_settings(guild_id)).get(key, default)

    @classmethod
    async def set_setting(cls, guild_id: str, key: str, value: str):
        await cls._execute(
            "INSERT OR REPLACE INTO settings (guild_id, key, value) VALUES (?, ?, ?)",
            (str(guild_id), key, value)
        )
        cls._invalidate(guild_id, "settings")

    # ── Events ────────────────────────────────────────────
    @classmethod
    async def log_event(cls, guild_id: str, event_type: str, data: dict):
        gid = str(guild_id)
        await cls._execute(
            "INSERT INTO events (guild_id, type, data, timestamp) VALUES (?, ?, ?, ?)",
            (gid, event_type, json.dumps(data, default=str), int(time.time() * 1000))
        )
        n = cls._event_counter.get(gid, 0) + 1
        cls._event_counter[gid] = n
        if n % 25 == 0:
            await cls._execute("""
                DELETE FROM events WHERE guild_id=? AND id NOT IN (
                    SELECT id FROM events WHERE guild_id=? ORDER BY id DESC LIMIT ?
                )
            """, (gid, gid, EVENT_RETENTION))

    @classmethod
    async def get_recent_events(cls, guild_id: str, limit: int = 10) -> list:
        rs = await cls._execute(
            "SELECT type, data, timestamp FROM events WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (str(guild_id), limit)
        )
        return [
            {"type": r[0], "data": json.loads(r[1]) if r[1] else {}, "timestamp": r[2]}
            for r in rs.rows
        ]

    # ── Lockdown state (ανά server) ───────────────────────
    @classmethod
    async def get_lockdown(cls, guild_id: str) -> dict | None:
        gid = str(guild_id)
        rs = await cls._execute(
            "SELECT kind, reason, actor_id, channels, roles, started FROM lockdown_state WHERE guild_id=?",
            (gid,)
        )
        if rs.rows:
            r = rs.rows[0]
            return {
                "kind": r[0], "reason": r[1], "actor_id": r[2],
                "channels": json.loads(r[3] or "{}"), "roles": json.loads(r[4] or "{}"),
                "started": r[5],
            }
        # Legacy: lockdown από παλιά έκδοση (αποθηκευμένο στα settings)
        if await cls.get_setting(gid, "lockdown_active", "0") == "1":
            ids  = json.loads(await cls.get_setting(gid, "lockdown_channels", "[]") or "[]")
            orig = json.loads(await cls.get_setting(gid, "lockdown_original_perms", "{}") or "{}")
            if ids:
                return {
                    "kind": "antinuke", "reason": "legacy lockdown", "actor_id": None,
                    "channels": {str(c): orig.get(str(c), {}) for c in ids},
                    "roles": {}, "started": int(time.time()),
                }
        return None

    @classmethod
    async def save_lockdown(cls, guild_id: str, kind: str, reason: str, actor_id,
                            channels: dict, roles: dict, started: int = None):
        await cls._execute(
            "INSERT OR REPLACE INTO lockdown_state "
            "(guild_id, kind, reason, actor_id, channels, roles, started) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(guild_id), kind, reason, str(actor_id) if actor_id else None,
             json.dumps(channels), json.dumps(roles), started or int(time.time()))
        )

    @classmethod
    async def clear_lockdown(cls, guild_id: str):
        gid = str(guild_id)
        await cls._execute("DELETE FROM lockdown_state WHERE guild_id=?", (gid,))
        if await cls.get_setting(gid, "lockdown_active", "0") == "1":
            await cls.set_setting(gid, "lockdown_active", "0")
            await cls.set_setting(gid, "lockdown_channels", "[]")
            await cls.set_setting(gid, "lockdown_original_perms", "{}")

    # ── Backups ───────────────────────────────────────────
    @classmethod
    async def save_backup(cls, guild_id: str, kind: str, label: str, data: str) -> int:
        rs = await cls._execute(
            "INSERT INTO backups (guild_id, kind, label, created, data) VALUES (?, ?, ?, ?, ?)",
            (str(guild_id), kind, label, int(time.time()), data)
        )
        return int(rs.last_insert_rowid or 0)

    @classmethod
    async def list_backups(cls, guild_id: str, limit: int = 25) -> list:
        rs = await cls._execute(
            "SELECT id, kind, label, created FROM backups WHERE guild_id=? ORDER BY id DESC LIMIT ?",
            (str(guild_id), limit)
        )
        return [{"id": r[0], "kind": r[1], "label": r[2], "created": r[3]} for r in rs.rows]

    @classmethod
    async def get_backup(cls, guild_id: str, backup_id: int) -> dict | None:
        rs = await cls._execute(
            "SELECT id, kind, label, created, data FROM backups WHERE guild_id=? AND id=?",
            (str(guild_id), int(backup_id))
        )
        if not rs.rows:
            return None
        r = rs.rows[0]
        return {"id": r[0], "kind": r[1], "label": r[2], "created": r[3], "data": r[4]}

    @classmethod
    async def latest_backup(cls, guild_id: str, before_ts: int = None,
                            kinds: tuple = ("auto", "manual")) -> dict | None:
        marks = ",".join("?" for _ in kinds)
        sql   = f"SELECT id, kind, label, created, data FROM backups WHERE guild_id=? AND kind IN ({marks})"
        args  = [str(guild_id), *kinds]
        if before_ts is not None:
            sql += " AND created <= ?"
            args.append(int(before_ts))
        sql += " ORDER BY id DESC LIMIT 1"
        rs = await cls._execute(sql, tuple(args))
        if not rs.rows:
            return None
        r = rs.rows[0]
        return {"id": r[0], "kind": r[1], "label": r[2], "created": r[3], "data": r[4]}

    @classmethod
    async def delete_backup(cls, guild_id: str, backup_id: int):
        await cls._execute(
            "DELETE FROM backups WHERE guild_id=? AND id=?", (str(guild_id), int(backup_id))
        )

    @classmethod
    async def prune_backups(cls, guild_id: str, kind: str, keep: int):
        await cls._execute("""
            DELETE FROM backups WHERE guild_id=? AND kind=? AND id NOT IN (
                SELECT id FROM backups WHERE guild_id=? AND kind=? ORDER BY id DESC LIMIT ?
            )
        """, (str(guild_id), kind, str(guild_id), kind, keep))

    # ── Quarantine ────────────────────────────────────────
    @classmethod
    async def save_quarantine(cls, guild_id: str, user_id: str, role_ids: list, reason: str):
        existing = await cls.get_quarantine(guild_id, user_id)
        merged   = sorted(set(existing["roles"] if existing else []) | {int(r) for r in role_ids})
        await cls._execute(
            "INSERT OR REPLACE INTO quarantined (guild_id, user_id, roles, reason, created) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(guild_id), str(user_id), json.dumps(merged), reason, int(time.time()))
        )

    @classmethod
    async def get_quarantine(cls, guild_id: str, user_id: str) -> dict | None:
        rs = await cls._execute(
            "SELECT roles, reason, created FROM quarantined WHERE guild_id=? AND user_id=?",
            (str(guild_id), str(user_id))
        )
        if not rs.rows:
            return None
        r = rs.rows[0]
        return {"roles": [int(x) for x in json.loads(r[0] or "[]")], "reason": r[1], "created": r[2]}

    @classmethod
    async def delete_quarantine(cls, guild_id: str, user_id: str):
        await cls._execute(
            "DELETE FROM quarantined WHERE guild_id=? AND user_id=?", (str(guild_id), str(user_id))
        )

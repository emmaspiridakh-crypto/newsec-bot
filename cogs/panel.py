import asyncio
import time

import discord
from discord.ext import commands
from discord import app_commands

from database import Database
from utils import health, lockdown, logger, ui
from utils.cv2_helper import (
    respond_cv2, update_cv2, edit_original_cv2, no_access, send_cv2,
)
from utils.modules import MODULES, CATEGORIES, modules_in
from utils.security import parse_pair

ALT_ACTIONS = ["kick", "log", "quarantine"]
PAGES = ("home", "anti", "automod", "alt", "backup", "logs", "wl", "health")


# ══ Context ══════════════════════════════════════════════════════════════════

async def load_ctx(bot, guild: discord.Guild, page: str) -> dict:
    gid = str(guild.id)
    ctx = {
        "guild":    guild,
        "icon":     str(guild.icon.url) if guild.icon else None,
        "cfg":      await Database.get_all_config(gid),
        "disabled": set(await Database.get_disabled_modules(gid)),
        "lockdown": await Database.get_lockdown(gid),
        "log_ch":   await Database.get_setting(gid, "log_channel_id"),
    }
    if page in ("home", "backup"):
        ctx["backups"] = await Database.list_backups(gid, 25)
    if page == "wl":
        ctx["owners"]  = await Database.get_server_owners(gid)
        ctx["trusted"] = await Database.get_trusted(gid)
        ctx["wl_u"]    = await Database.get_whitelist_users(gid)
        ctx["wl_b"]    = await Database.get_whitelist_bots(gid)
        ctx["wl_c"]    = await Database.get_whitelist_channels(gid)
        ctx["wl_r"]    = await Database.get_whitelist_roles(gid)
    if page == "health":
        ctx["health"] = await health.run_checks(bot, guild)
    return ctx


def _on(ctx, key) -> bool:
    return key not in ctx["disabled"]


def _st(ctx, key) -> str:
    return "ON" if _on(ctx, key) else "OFF"


def _toggles(ctx, keys: list, page: str) -> list:
    """Κουμπιά ON/OFF σε σειρές των 5."""
    buttons = [ui.btn(MODULES[k][0], f"pn:tg:{k}:{page}", 3 if _on(ctx, k) else 4) for k in keys]
    return [ui.row(*buttons[i:i + 5]) for i in range(0, len(buttons), 5)]


def _back() -> dict:
    return ui.btn("◀ Back", "pn:go:home", 2)


def _head(title: str, sub: str, ctx: dict) -> dict:
    return ui.header(f"> {title}\n{sub}", ctx["icon"])


# ══ Pages ════════════════════════════════════════════════════════════════════

def page_home(ctx: dict) -> list:
    guild, ld = ctx["guild"], ctx["lockdown"]
    lines = []
    if ld:
        lines.append(f"• **LOCKDOWN ACTIVE** — since <t:{ld['started']}:R>  ({ld['kind']})")
    else:
        lines.append("• Lockdown: inactive")
    for cat, name in CATEGORIES.items():
        keys = modules_in(cat)
        n_on = sum(1 for k in keys if _on(ctx, k))
        lines.append(f"• {name}: {n_on}/{len(keys)} modules ON")
    last = next((b for b in ctx.get("backups", []) if b["kind"] in ("auto", "manual")), None)
    last_txt = "<t:%d:R>" % last["created"] if last else "none"
    log_txt  = "<#%s>" % ctx["log_ch"] if ctx["log_ch"] else "not set"
    lines.append("• Last backup: " + last_txt)
    lines.append("• Log channel: " + log_txt)

    return [ui.container([
        _head("Security Control Panel", f"**{guild.name}**", ctx),
        ui.sep(),
        ui.text("\n".join(lines)),
        ui.sep(),
        ui.row(ui.btn("Anti-Nuke", "pn:go:anti", 1), ui.btn("AutoMod", "pn:go:automod", 1),
               ui.btn("ALT & Bots", "pn:go:alt", 1), ui.btn("Backup", "pn:go:backup", 1),
               ui.btn("Logs", "pn:go:logs", 1)),
        ui.row(ui.btn("Whitelist", "pn:go:wl", 2), ui.btn("Health", "pn:go:health", 2),
               ui.btn("PANIC", "pn:panic", 4),
               ui.btn("Restore", f"lk_restore_{guild.id}_p", 3, disabled=not ld)),
    ], 0x8B0000)]


def page_anti(ctx: dict) -> list:
    c = ctx["cfg"]
    body = (
        f"**Punishment:** strip dangerous roles → timeout {c['nuke_timeout_hours']}h "
        "(→ Quarantine role if timeout is impossible). Bots: strip + kick.\n"
        "Every trigger also locks the server and DMs the owner.\n\n"
        f"• **Mass Action** [{_st(ctx,'mass_action')}] — {c['mass_action_limit']}/{c['mass_action_window']}s "
        "(ban, kick, channel delete, webhooks, prune, bot add, server edits)\n"
        f"• **Role Guard** [{_st(ctx,'role_action')}] — {c['role_action_limit']}/{c['role_action_window']}s "
        "(role create/delete, permission escalation)\n"
        f"• **Channel Guard** [{_st(ctx,'channel_guard')}] — {c['chan_action_limit']}/{c['chan_action_window']}s "
        "(channel create / edit)\n"
        f"• **Emoji Guard** [{_st(ctx,'emoji_guard')}] — {c['emoji_action_limit']}/{c['emoji_action_window']}s\n"
        f"• **Purge Guard** [{_st(ctx,'purge_guard')}] — {c['purge_limit']}/{c['purge_window']}s (bulk deletes)\n"
        f"• **Invite Guard** [{_st(ctx,'invite_guard')}] — {c['invite_action_limit']}/{c['invite_action_window']}s\n"
        f"• **Everyone Guard** [{_st(ctx,'everyone_guard')}] — {c['everyone_limit']}/{c['everyone_window']}s (@everyone/@here)\n"
        f"• **Escalation Guard** [{_st(ctx,'escalation_guard')}] — dangerous role given to a member"
    )
    return [ui.container([
        _head("Anti-Nuke", "Limits are `actions/seconds`.", ctx), ui.sep(), ui.text(body), ui.sep(),
        *_toggles(ctx, modules_in("anti"), "anti"),
        ui.row(ui.btn("Core Limits", "pn:md:core", 1), ui.btn("Guard Limits", "pn:md:guards", 1), _back()),
    ], 0x8B0000)]


def page_automod(ctx: dict) -> list:
    c = ctx["cfg"]
    body = (
        f"• **Link Filter** [{_st(ctx,'link')}] — delete + {c['link_timeout_mins']} min timeout (also on edited messages)\n"
        f"• **Token Filter** [{_st(ctx,'token')}] — delete Discord tokens (also on edits)\n"
        f"• **Spam Filter** [{_st(ctx,'spam')}] — {c['spam_threshold']} msgs / {c['spam_window_secs']}s → "
        f"{c['spam_timeout_mins']} min timeout\n"
        f"• **Bot Scan** [{_st(ctx,'automod_bots')}] — bots/webhooks are filtered too unless whitelisted "
        "(`/whitelist bot`, `/whitelist channel`)"
    )
    return [ui.container([
        _head("AutoMod", "Message filters.", ctx), ui.sep(), ui.text(body), ui.sep(),
        *_toggles(ctx, modules_in("automod"), "automod"),
        ui.row(ui.btn("Edit Settings", "pn:md:automod", 1), _back()),
    ], 0xE67E22)]


def page_alt(ctx: dict) -> list:
    c = ctx["cfg"]
    body = (
        f"• **ALT Detection** [{_st(ctx,'alt')}] — score ≥ {c['alt_score_threshold']} → action: "
        f"**{c['alt_action']}**\n"
        f"> Score: account younger than {c['alt_age_days']}d (+50), under 7d (+20), under "
        f"{int(c['alt_age_days']) * 2}d (+25), default avatar (+20), generated-looking name (+15), "
        "no badges (+10).\n"
        f"• **Bot Verify** [{_st(ctx,'bot_verify')}] — new bots get zero permissions until you Accept "
        "(accepted bots are also whitelisted for Anti-Nuke/AutoMod)"
    )
    return [ui.container([
        _head("ALT & Bots", "New accounts and new bots.", ctx), ui.sep(), ui.text(body), ui.sep(),
        *_toggles(ctx, modules_in("alt"), "alt"),
        ui.row(ui.btn(f"Action: {c['alt_action'].capitalize()}", "pn:alt_action", 2),
               ui.btn("Edit Settings", "pn:md:alt", 1), _back()),
    ], 0x5865F2)]


def page_backup(ctx: dict) -> list:
    items = ctx.get("backups", [])
    listing = "\n".join(f"• **#{b['id']}**  `{b['kind']}`  <t:{b['created']}:R>  —  {b['label']}"
                        for b in items[:6]) or "No backups yet."
    body = (
        f"• **Auto Backup** [{_st(ctx,'auto_backup')}] — hourly + 45s after any role/channel change "
        "(paused during an attack or lockdown)\n"
        f"• **Auto Restore** [{_st(ctx,'auto_restore')}] — after a channel/role delete attack, missing "
        "channels/roles are recreated from the last clean backup\n\n"
        f"{listing}"
    )
    children = [
        _head("Backup", "Roles and channels.", ctx), ui.sep(), ui.text(body), ui.sep(),
        *_toggles(ctx, modules_in("backup"), "backup"),
    ]
    if items:
        opts = [{"label": f"#{b['id']} · {b['kind']} · {b['label']}"[:100], "value": str(b["id"]),
                 "description": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(b["created"]))}
                for b in items]
        children.append(ui.row(ui.string_select("pn:bk_pick", opts, "Restore from a backup…")))
    children.append(ui.row(ui.btn("Create Backup", "pn:bk_create", 1), _back()))
    return [ui.container(children, 0x57F287)]


def page_logs(ctx: dict) -> list:
    log_txt = "<#%s>" % ctx["log_ch"] if ctx["log_ch"] else "**not set** — pick one below"
    body = (
        f"• Log channel: {log_txt}\n"
        f"• **Members** [{_st(ctx,'log_members')}] — joins, leaves, bans, kicks, nicknames, roles, timeouts\n"
        f"• **Server** [{_st(ctx,'log_server')}] — channels, roles, emojis, invites, webhooks, server settings\n"
        f"• **Security** [{_st(ctx,'log_security')}] — Anti-Nuke, AutoMod, ALT, lockdown, backups\n"
        f"• **Bot** [{_st(ctx,'log_bot')}] — bots added/removed, bot verification, commands used\n"
        "> Lockdown alerts (with the Restore button) always go to the log channel."
    )
    return [ui.container([
        _head("Logs", "One channel, four categories.", ctx), ui.sep(), ui.text(body), ui.sep(),
        *_toggles(ctx, modules_in("logs"), "logs"),
        ui.row(ui.channel_select("pn:logch", "Set the log channel…")),
        ui.row(ui.btn("Recent Events", "pn:recent", 2), _back()),
    ], 0x5865F2)]


def page_wl(ctx: dict) -> list:
    def fu(ids):  return ", ".join(f"<@{i}>" for i in ids[:10]) + ("…" if len(ids) > 10 else "") if ids else "None"
    def fr(ids):  return ", ".join(f"<@&{i}>" for i in ids[:10]) if ids else "None"
    def fc(ids):  return ", ".join(f"<#{i}>" for i in ids[:10]) if ids else "None"
    def fb(ids):  return ", ".join(f"`{i}`" for i in ids[:10]) if ids else "None"
    body = (
        f"**Server owners** (+ the real server owner and the installer): {fu(ctx['owners'])}\n"
        f"**Trusted admins** (exempt from Anti-Nuke): {fu(ctx['trusted'])}\n"
        f"**Whitelisted users** (links): {fu(ctx['wl_u'])}\n"
        f"**Whitelisted roles** (links): {fr(ctx['wl_r'])}\n"
        f"**Whitelisted channels**: {fc(ctx['wl_c'])}\n"
        f"**Whitelisted bots** (skip verify, Anti-Nuke and Bot Scan): {fb(ctx['wl_b'])}\n\n"
        "> Manage with `/trusted`, `/whitelist`, `/unwhitelist`, `/addserverowner`."
    )
    return [ui.container([
        _head("Whitelist", "Who is exempt from what.", ctx), ui.sep(), ui.text(body), ui.sep(),
        ui.row(_back()),
    ], 0x5865F2)]


def page_health(ctx: dict) -> list:
    results = ctx["health"]
    color = 0xED4245 if any(l == health.FAIL for l, _ in results) else \
            0xE67E22 if any(l == health.WARN for l, _ in results) else 0x57F287
    return [ui.container([
        _head("Health", "Is the bot set up to protect this server?", ctx), ui.sep(),
        ui.text(health.render(results)), ui.sep(),
        ui.row(ui.btn("Refresh", "pn:go:health", 1), _back()),
    ], color)]


def page_panic_confirm(ctx: dict) -> list:
    return [ui.container([
        ui.text(
            "> PANIC\n"
            "This will immediately:\n"
            "• Lock **every channel** (no sending / no voice connect) for @everyone\n"
            "• Remove dangerous permissions from **every role** below the bot\n"
            "• Save the previous state so the **Restore** button brings everything back exactly\n"
            "• Take a backup and DM the owner\n\n"
            "Only this server is affected. Continue?"),
        ui.sep(),
        ui.row(ui.btn("Confirm PANIC", "pn:panic_yes", 4), ui.btn("Cancel", "pn:go:home", 2)),
    ], 0xED4245)]


PAGE_BUILDERS = {
    "home": page_home, "anti": page_anti, "automod": page_automod, "alt": page_alt,
    "backup": page_backup, "logs": page_logs, "wl": page_wl, "health": page_health,
}


async def render_page(bot, guild: discord.Guild, page: str) -> list:
    page = page if page in PAGE_BUILDERS else "home"
    return PAGE_BUILDERS[page](await load_ctx(bot, guild, page))


# ══ Status / Logs / Help ═════════════════════════════════════════════════════

def build_status(ctx: dict) -> list:
    lines = []
    for cat, name in CATEGORIES.items():
        lines.append(f"> {name}")
        for k in modules_in(cat):
            lines.append(f"• {MODULES[k][0]}  [{_st(ctx, k)}]")
    ld = ctx["lockdown"]
    lines.append(f"\n• Lockdown: {'ACTIVE' if ld else 'inactive'}")
    return [ui.container([_head("Security Status", "", ctx), ui.sep(), ui.text("\n".join(lines))], 0x5865F2)]


TYPE_LABEL = {
    "token_detected": "Token Detected", "link_detected": "Link Detected",
    "spam_detected": "Spam Detected", "alt_detected": "Alt Detected", "bot_join": "Bot Join",
    "mass_action": "Mass Action", "channel_delete": "Channel Delete",
    "perm_escalation": "Permission Escalation", "lockdown": "Lockdown",
    "lockdown_lifted": "Lockdown Lifted", "webhook_spam": "Webhook Spam",
    "dangerous_bot_add": "Dangerous Bot Added", "guild_vandalism": "Server Settings Changed",
    "everyone_blocked": "@everyone Blocked", "bot_message_blocked": "Bot Message Blocked",
    "auto_restore": "Auto Restore", "backup_restore": "Backup Restored", "released": "Member Released",
    "panic": "Panic Lockdown",
}


def build_logs(events: list, guild_icon: str | None) -> list:
    if not events:
        body = "No security events logged yet."
    else:
        lines = []
        for e in events:
            who   = e["data"].get("user") or e["data"].get("bot") or "unknown"
            label = TYPE_LABEL.get(e["type"], e["type"])
            lines.append(f"• **{label}**  —  {who}  <t:{e['timestamp'] // 1000}:R>")
        body = "\n".join(lines)
    return [ui.container([ui.header("> Recent Security Events", guild_icon), ui.sep(), ui.text(body)], 0x8B0000)]


def build_help(is_owner: bool) -> list:
    owner_block = (
        "> Owner Commands\n"
        "• /addserverowner <id>  —  Add Server Owner\n"
        "• /removeserverowner <id>  —  Remove Server Owner\n"
        "• /trusted add|remove|list  —  Trusted admins (exempt from Anti-Nuke)\n"
        "• /add user @user  —  Add whitelist user\n"
        "• /remove user @user  —  Remove whitelist user\n"
        "• /release @member  —  Lift quarantine / punishment\n\n"
    ) if is_owner else ""
    body = (
        f"{owner_block}"
        "> Panel  (Owner only)\n"
        "• /panel  —  Control panel (categories, Panic, Restore)\n"
        "• /status  —  Module overview\n"
        "• /securitycheck  —  Health check\n"
        "• /logs [amount]  —  Recent security events\n"
        "• /setlogchannel #channel  —  Set the log channel\n"
        "• /unlock  —  Lift a lockdown\n\n"
        "> Backup  (Owner only)\n"
        "• /backup create|list|restore|delete\n\n"
        "> Config & Whitelist  (Owner only)\n"
        "• /config  —  View / change settings\n"
        "• /whitelist channel|role|bot|list  •  /unwhitelist channel|role|bot\n\n"
        "• /sechelp  —  This message"
    )
    return [ui.container([ui.text("> Security Bot — Commands"), ui.sep(), ui.text(body)], 0x5865F2)]


# ── All Servers ────────────────────────────────────────────────────────────────

ALLSERVERS_PAGE_SIZE = 8


def build_allservers(guilds: list, page: int) -> list:
    total      = len(guilds)
    pages      = max(1, (total + ALLSERVERS_PAGE_SIZE - 1) // ALLSERVERS_PAGE_SIZE)
    page       = max(0, min(page, pages - 1))
    start      = page * ALLSERVERS_PAGE_SIZE
    page_items = guilds[start:start + ALLSERVERS_PAGE_SIZE]

    # Το header ήταν Section (type 9) χωρίς accessory — το Discord API απαιτεί
    # accessory σε κάθε Section, γι' αυτό το /allservers έσκαγε πάντα με 400.
    # Δεν χρειάζεται accessory εδώ, οπότε το κάνουμε απλό Text Display (type 10).
    header = {"type": 10, "content": f"> All Servers ({total})\nPage {page + 1}/{pages}"}

    sections = []
    for guild in page_items:
        section = {
            "type": 9,
            "components": [{"type": 10, "content": (
                f"**{guild.name}**\n"
                f"> ID: `{guild.id}`  •  Members: {guild.member_count}"
            )}],
            "accessory": {
                "type": 2,
                "label": "Leave Server",
                "style": 4,
                "custom_id": f"allsrv_leave_{guild.id}_{page}"
            }
        }
        sections.append(section)

    nav_row = {
        "type": 1,
        "components": [
            {"type": 2, "label": "◀ Prev", "style": 2, "custom_id": f"allsrv_page_{page - 1}", "disabled": page <= 0},
            {"type": 2, "label": "Next ▶", "style": 2, "custom_id": f"allsrv_page_{page + 1}", "disabled": page >= pages - 1},
        ]
    }

    return [{
        "type": 17,
        "accent_color": 0x5865F2,
        "components": [header, {"type": 14}, *sections, {"type": 14}, nav_row]
    }]


BANLIST_PAGE_SIZE = 8


def build_banlist(entries: list, page: int) -> list:
    total      = len(entries)
    pages      = max(1, (total + BANLIST_PAGE_SIZE - 1) // BANLIST_PAGE_SIZE)
    page       = max(0, min(page, pages - 1))
    start      = page * BANLIST_PAGE_SIZE
    page_items = entries[start:start + BANLIST_PAGE_SIZE]

    header = {"type": 10, "content": f"> Banned Users — All Servers ({total})\nPage {page + 1}/{pages}"}

    sections = []
    if not page_items:
        sections.append({"type": 10, "content": "Δεν βρέθηκαν ban σε κανένα server."})
    for user, guild, reason in page_items:
        sections.append({"type": 10, "content": (
            f"**{user}**  (`{user.id}`)\n"
            f"> Server: {guild.name} (`{guild.id}`)\n"
            f"> Reason: {reason or '—'}"
        )})

    nav_row = {
        "type": 1,
        "components": [
            {"type": 2, "label": "◀ Prev", "style": 2, "custom_id": f"banlist_page_{page - 1}", "disabled": page <= 0},
            {"type": 2, "label": "Next ▶", "style": 2, "custom_id": f"banlist_page_{page + 1}", "disabled": page >= pages - 1},
        ]
    }

    return [{
        "type": 17,
        "accent_color": 0xED4245,
        "components": [header, {"type": 14}, *sections, {"type": 14}, nav_row]
    }]


async def _collect_all_bans(bot: commands.Bot) -> list:
    entries = []
    for guild in bot.guilds:
        try:
            async for ban_entry in guild.bans(limit=None):
                entries.append((ban_entry.user, guild, ban_entry.reason))
        except discord.Forbidden:
            continue
        except discord.HTTPException:
            continue
    entries.sort(key=lambda e: (e[1].name.lower(), str(e[0])))
    return entries




# ══ Modals ═══════════════════════════════════════════════════════════════════

# spec: ("pair", limit_key, window_key, label) | ("int", key, label, min, max)
MODAL_SPECS = {
    "core": ("Anti-Nuke Core Limits", "anti", [
        ("pair", "mass_action_limit", "mass_action_window", "Mass Action (actions/seconds)"),
        ("pair", "role_action_limit", "role_action_window", "Role Guard (actions/seconds)"),
        ("int", "nuke_timeout_hours", "Punishment timeout (hours, 1-672)", 1, 672),
    ]),
    "guards": ("Guard Limits", "anti", [
        ("pair", "chan_action_limit",   "chan_action_window",   "Channel Guard (actions/seconds)"),
        ("pair", "emoji_action_limit",  "emoji_action_window",  "Emoji Guard (actions/seconds)"),
        ("pair", "invite_action_limit", "invite_action_window", "Invite Guard (actions/seconds)"),
        ("pair", "purge_limit",         "purge_window",         "Purge Guard (actions/seconds)"),
        ("pair", "everyone_limit",      "everyone_window",      "Everyone Guard (actions/seconds)"),
    ]),
    "automod": ("AutoMod Settings", "automod", [
        ("pair", "spam_threshold", "spam_window_secs", "Spam (messages/seconds)"),
        ("int", "spam_timeout_mins", "Spam timeout (minutes)", 1, 40320),
        ("int", "link_timeout_mins", "Link timeout (minutes)", 1, 40320),
    ]),
    "alt": ("ALT Detection Settings", "alt", [
        ("int", "alt_age_days", "Account age threshold (days)", 1, 365),
        ("int", "alt_score_threshold", "Score needed to trigger (10-200)", 10, 200),
    ]),
}


class ConfigModal(discord.ui.Modal):
    def __init__(self, bot, cfg: dict, name: str):
        title, self.page, self.spec = MODAL_SPECS[name]
        super().__init__(title=title)
        self.bot    = bot
        self.inputs = []
        for item in self.spec:
            if item[0] == "pair":
                default, label = f"{cfg[item[1]]}/{cfg[item[2]]}", item[3]
            else:
                default, label = str(cfg[item[1]]), item[2]
            ti = discord.ui.TextInput(label=label[:45], default=default, required=True, max_length=12)
            self.inputs.append(ti)
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        if not await Database.is_server_owner(gid, str(interaction.user.id), self.bot.installer_id):
            await respond_cv2(interaction, [ui.container([ui.text("> Access Denied")], 0xED4245)], ephemeral=True)
            return
        updates, errors = {}, []
        for item, ti in zip(self.spec, self.inputs):
            if item[0] == "pair":
                pair = parse_pair(ti.value)
                if not pair:
                    errors.append(f"`{item[3]}` must look like `3/10` (both numbers ≥ 1)")
                else:
                    updates[item[1]], updates[item[2]] = str(pair[0]), str(pair[1])
            else:
                try:
                    v = int(ti.value.strip())
                    if not item[3] <= v <= item[4]:
                        raise ValueError
                    updates[item[1]] = str(v)
                except ValueError:
                    errors.append(f"`{item[2]}` must be a number between {item[3]} and {item[4]}")
        if errors:
            await respond_cv2(interaction, [ui.container(
                [ui.text("> Invalid value\n" + "\n".join(f"• {e}" for e in errors))], 0xED4245)],
                ephemeral=True)
            return
        for k, v in updates.items():
            await Database.set_config(gid, k, v)
        await update_cv2(interaction, await render_page(self.bot, interaction.guild, self.page))


# ══ Cog ══════════════════════════════════════════════════════════════════════

class Panel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    def _is_installer(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == str(self.bot.installer_id)

    def _is_main_server(self, interaction: discord.Interaction) -> bool:
        main_id = getattr(self.bot, "main_server_id", None)
        return bool(main_id) and str(interaction.guild_id) == str(main_id)

    def _icon(self, guild: discord.Guild) -> str | None:
        return str(guild.icon.url) if guild and guild.icon else None

    # ── /allservers ───────────────────────────────────────
    @app_commands.command(name="allservers", description="[Installer Only] List every server the bot is in (main server only)")
    async def allservers(self, interaction: discord.Interaction):
        if not self._is_installer(interaction):
            await no_access(interaction, "Μόνο ο Installer μπορεί να χρησιμοποιήσει το /allservers."); return
        if not self._is_main_server(interaction):
            await no_access(interaction, "Η εντολή /allservers δουλεύει μόνο στον main server."); return
        await interaction.response.defer(ephemeral=True)
        guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())
        await edit_original_cv2(interaction, build_allservers(guilds, 0), ephemeral=True)

    # ── /banlist ────────────────────────────────────────────
    @app_commands.command(name="banlist", description="[Installer Only] List every banned user across every server")
    async def banlist(self, interaction: discord.Interaction):
        if not self._is_installer(interaction):
            await no_access(interaction, "Μόνο ο Installer μπορεί να χρησιμοποιήσει το /banlist."); return
        if not self._is_main_server(interaction):
            await no_access(interaction, "Η εντολή /banlist δουλεύει μόνο στον main server."); return
        await interaction.response.defer(ephemeral=True)
        entries = await _collect_all_bans(self.bot)
        await edit_original_cv2(interaction, build_banlist(entries, 0), ephemeral=True)

    # ── /panel ────────────────────────────────────────────
    @app_commands.command(name="panel", description="Security Control Panel")
    async def panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        await edit_original_cv2(interaction, await render_page(self.bot, interaction.guild, "home"),
                                ephemeral=True)

    # ── /status ───────────────────────────────────────────
    @app_commands.command(name="status", description="Security status overview")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        ctx = await load_ctx(self.bot, interaction.guild, "status")
        await edit_original_cv2(interaction, build_status(ctx), ephemeral=True)

    # ── /logs ─────────────────────────────────────────────
    @app_commands.command(name="logs", description="Recent security events")
    @app_commands.describe(amount="Number of events (1-20, default 10)")
    async def logs(self, interaction: discord.Interaction, amount: int = 10):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        events = await Database.get_recent_events(gid, min(max(amount, 1), 20))
        await edit_original_cv2(interaction, build_logs(events, self._icon(interaction.guild)), ephemeral=True)

    # ── /sechelp ──────────────────────────────────────────
    @app_commands.command(name="sechelp", description="All security bot commands")
    async def sechelp(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid      = str(interaction.guild_id)
        is_owner = await self._is_owner(interaction.guild_id, interaction.user.id)
        is_wl    = await Database.is_whitelist_user(gid, str(interaction.user.id))
        if not is_owner and not is_wl:
            await no_access(interaction); return
        await edit_original_cv2(interaction, build_help(is_owner), ephemeral=True)

    # ── Helpers για buttons ───────────────────────────────
    async def _deny(self, interaction, msg="Server Owners only."):
        await respond_cv2(interaction, [ui.container(
            [ui.text(f"> Access Denied\n{msg}")], 0xED4245)], ephemeral=True)

    async def _panic(self, interaction: discord.Interaction):
        guild, gid = interaction.guild, str(interaction.guild_id)
        await update_cv2(interaction, [ui.container(
            [ui.text("> PANIC engaged\nLocking channels and stripping dangerous permissions…")], 0xED4245)])
        reason = f"Panic button pressed by {interaction.user}"
        lock = await lockdown.engage(self.bot, guild, kind="panic", reason=reason,
                                     actor_id=interaction.user.id)
        dm_line = await lockdown.notify_owners(self.bot, guild, f"PANIC lockdown in {guild.name}", [
            f"**By:** {interaction.user} ({interaction.user.id})",
            f"**Channels locked:** {lock['channels_locked']}/{lock['channels_total']}",
            f"**Roles stripped of dangerous perms:** {lock['roles_stripped']}/{lock['roles_total']}",
            "Use the **Restore** button in the log channel or `/panel` to lift it.",
        ])
        await Database.log_event(gid, "panic", {"user": str(interaction.user), "id": str(interaction.user.id)})
        cid = await Database.get_setting(gid, "log_channel_id")
        if cid:
            await send_cv2(int(cid), lockdown.alert_components(
                guild.id, title="PANIC LOCKDOWN", actor=interaction.user, reason=reason,
                punish=None, lock=lock, dm_line=dm_line, accent=0xE67E22))
        await edit_original_cv2(interaction, await render_page(self.bot, guild, "home"))

    async def _restore_lockdown(self, interaction: discord.Interaction):
        gid = str(interaction.guild_id)
        await interaction.response.defer()
        stats = await lockdown.restore(self.bot, interaction.guild, actor=interaction.user)
        if stats:
            await logger.log(gid, "security", "Lockdown lifted", [
                f"• By: {interaction.user.mention}",
                f"• Channels restored: {stats['channels_locked']}/{stats['channels_total']}",
                f"• Roles restored: {stats['roles_stripped']}/{stats['roles_total']}"])
        await edit_original_cv2(interaction, await render_page(self.bot, interaction.guild, "home"))

    # ── Button / select interactions ──────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component or not interaction.guild_id:
            return
        cid = interaction.data.get("custom_id", "")

        # ── Παλιά panels (πριν την ανανέωση) ──
        if cid.startswith("sec_"):
            await respond_cv2(interaction, [ui.container(
                [ui.text("> Outdated panel\nRun `/panel` again — the panel has been redesigned.")],
                0xE67E22)], ephemeral=True)
            return

        # ── All Servers / Ban List (installer, main server) ──
        if cid.startswith("allsrv_page_") or cid.startswith("allsrv_leave_") or cid.startswith("banlist_page_"):
            if not self._is_installer(interaction):
                await self._deny(interaction, "Μόνο ο Installer."); return
            if not self._is_main_server(interaction):
                await self._deny(interaction, "Μόνο στον main server."); return

            if cid.startswith("allsrv_page_"):
                page = int(cid.replace("allsrv_page_", ""))
                guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())
                await update_cv2(interaction, build_allservers(guilds, page))
                return
            if cid.startswith("banlist_page_"):
                page = int(cid.replace("banlist_page_", ""))
                await update_cv2(interaction, build_banlist(await _collect_all_bans(self.bot), page))
                return
            rest = cid.replace("allsrv_leave_", "")
            guild_id_str, _, page_str = rest.rpartition("_")
            page = int(page_str) if page_str.isdigit() else 0
            target = self.bot.get_guild(int(guild_id_str))
            if target:
                try:
                    await target.leave()
                except Exception as e:
                    await respond_cv2(interaction, [ui.container(
                        [ui.text(f"> Error\nΔεν μπόρεσα να αφήσω το server: {e}")], 0xED4245)], ephemeral=True)
                    return
            guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())
            await update_cv2(interaction, build_allservers(guilds, page))
            return

        gid      = str(interaction.guild_id)
        is_owner = await self._is_owner(interaction.guild_id, interaction.user.id)
        is_admin = interaction.user.guild_permissions.administrator

        # ── Bot Accept / Deny ──────────────────────────────
        if cid.startswith("bot_accept_") or cid.startswith("bot_deny_"):
            if not is_owner and not is_admin:
                await self._deny(interaction, "Administrators only."); return
            bot_id     = cid.replace("bot_accept_", "").replace("bot_deny_", "")
            bot_member = interaction.guild.get_member(int(bot_id))
            name       = bot_member.name if bot_member else bot_id
            avatar     = str(bot_member.display_avatar.url) if bot_member else None

            if cid.startswith("bot_accept_"):
                # Απαντάμε αμέσως (3s όριο) και ολοκληρώνουμε τη δουλειά μετά
                await update_cv2(interaction, [ui.container(
                    [ui.text(f"> Accepting **{name}**…")], 0xE67E22)])
                await Database.add_whitelist_bot(gid, bot_id)
                if bot_member:
                    await asyncio.gather(*[ch.set_permissions(bot_member, overwrite=None, reason="Bot accepted")
                                           for ch in interaction.guild.channels], return_exceptions=True)
                comps = [ui.container([ui.header(
                    f"> Bot Accepted\n• Bot: **{name}**\n• Accepted by: {interaction.user.mention}\n"
                    "• Whitelisted for Anti-Nuke and AutoMod.", avatar)], 0x57F287)]
                await edit_original_cv2(interaction, comps)
                await logger.log(gid, "bot", "Bot accepted", [
                    f"• Bot: **{name}**  ({bot_id})", f"• By: {interaction.user.mention}"])
            else:
                kicked = False
                if bot_member:
                    try:
                        await bot_member.kick(reason=f"Bot denied by {interaction.user}")
                        kicked = True
                    except Exception:
                        pass
                comps = [ui.container([ui.header(
                    f"> Bot Denied\n• Bot: **{name}**\n• Denied by: {interaction.user.mention}\n"
                    f"• Kicked: {'Yes' if kicked else 'No'}", avatar)], 0xED4245)]
                await update_cv2(interaction, comps)
                await logger.log(gid, "bot", "Bot denied", [
                    f"• Bot: **{name}**  ({bot_id})", f"• By: {interaction.user.mention}",
                    f"• Kicked: {'yes' if kicked else 'no'}"], color=0xED4245)
            return

        # ── Lockdown Restore (από το panel) ────────────────
        if cid.startswith("lk_restore_") and cid.endswith("_p"):
            if cid[len("lk_restore_"):-2] != gid:
                await self._deny(interaction, "This button belongs to another server."); return
            if not is_owner:
                await self._deny(interaction); return
            await self._restore_lockdown(interaction)
            return

        if not cid.startswith("pn:"):
            return                       # δεν είναι δικό μας (backup/antinuke/bot_status χειρίζονται τα δικά τους)
        if not is_owner:
            await self._deny(interaction); return

        parts = cid.split(":")
        action = parts[1]

        if action == "go":
            await update_cv2(interaction, await render_page(self.bot, interaction.guild, parts[2]))
        elif action == "tg":
            module, page = parts[2], parts[3]
            if module in MODULES:
                if await Database.is_module_enabled(gid, module):
                    await Database.disable_module(gid, module)
                else:
                    await Database.enable_module(gid, module)
            await update_cv2(interaction, await render_page(self.bot, interaction.guild, page))
        elif action == "alt_action":
            cur = await Database.get_config(gid, "alt_action", "kick")
            nxt = ALT_ACTIONS[(ALT_ACTIONS.index(cur) + 1) % len(ALT_ACTIONS)] if cur in ALT_ACTIONS else "kick"
            await Database.set_config(gid, "alt_action", nxt)
            await update_cv2(interaction, await render_page(self.bot, interaction.guild, "alt"))
        elif action == "md":
            cfg = await Database.get_all_config(gid)
            await interaction.response.send_modal(ConfigModal(self.bot, cfg, parts[2]))
        elif action == "panic":
            await update_cv2(interaction, page_panic_confirm({}))
        elif action == "panic_yes":
            await self._panic(interaction)
        elif action == "bk_create":
            await interaction.response.defer()
            backup = self.bot.get_cog("Backup")
            if backup:
                await backup.create_backup(interaction.guild, "manual", "Created from panel")
            await edit_original_cv2(interaction, await render_page(self.bot, interaction.guild, "backup"))
        elif action == "bk_pick":
            bid = int(interaction.data["values"][0])
            b = await Database.get_backup(gid, bid)
            if not b:
                await respond_cv2(interaction, [ui.container([ui.text("> Backup no longer exists.")], 0xED4245)],
                                  ephemeral=True)
                return
            from cogs.backup import unpack, summarize
            info = {**summarize(unpack(b["data"])), "kind": b["kind"], "created": b["created"]}
            if await Database.get_lockdown(gid):
                await update_cv2(interaction, [ui.container([
                    ui.text("> Lockdown active\nLift the lockdown first (Restore), then restore the backup."),
                    ui.sep(), ui.row(ui.btn("◀ Back", "pn:go:backup", 2))], 0xED4245)])
                return
            comps = ui.restore_confirm(gid, bid, info)
            comps[0]["components"][-1]["components"][1] = ui.btn("Cancel", "pn:go:backup", 2)
            await update_cv2(interaction, comps)
        elif action == "logch":
            await Database.set_setting(gid, "log_channel_id", str(interaction.data["values"][0]))
            await update_cv2(interaction, await render_page(self.bot, interaction.guild, "logs"))
        elif action == "recent":
            events = await Database.get_recent_events(gid, 10)
            await respond_cv2(interaction, build_logs(events, self._icon(interaction.guild)), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Panel(bot))

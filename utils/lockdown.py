"""Lockdown / Panic / Restore — πάντα ανά server (guild_id σε κάθε κλήση και στη βάση)."""
import asyncio
import time

import discord

from database import Database
from utils import ui
from utils.security import is_dangerous, strip_dangerous

LOCK_TYPES = (discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.ForumChannel)
_VOICE     = (discord.VoiceChannel, discord.StageChannel)
_OW_KEYS   = ("send_messages", "send_messages_in_threads", "connect")


# ── Backup pause (δεν παίρνουμε auto-backup όσο ο server δέχεται επίθεση) ──
def pause_backups(bot, guild_id, secs: int = 900):
    pause = getattr(bot, "backup_pause", None)
    if pause is None:
        pause = bot.backup_pause = {}
    pause[str(guild_id)] = time.time() + secs


def backups_paused(bot, guild_id) -> bool:
    return getattr(bot, "backup_pause", {}).get(str(guild_id), 0) > time.time()


def resume_backups(bot, guild_id):
    getattr(bot, "backup_pause", {}).pop(str(guild_id), None)


# ── Planning ────────────────────────────────────────────────
def _lockable_roles(guild: discord.Guild) -> list:
    mine = set(guild.me.roles)
    return [
        r for r in guild.roles
        if r not in mine and r.position < guild.me.top_role.position and is_dangerous(r.permissions)
    ]


def _plan(guild, channels, roles, ch_state: dict, role_state: dict, reason: str):
    """Αποθηκεύει τις ΑΡΧΙΚΕΣ τιμές (μόνο την πρώτη φορά) και επιστρέφει τα coroutines."""
    ch_tasks, role_tasks = [], []
    for ch in channels:
        ow  = ch.overwrites_for(guild.default_role)
        key = str(ch.id)
        if key not in ch_state:
            ch_state[key] = {k: getattr(ow, k) for k in _OW_KEYS}
        ow.send_messages            = False
        ow.send_messages_in_threads = False
        if isinstance(ch, _VOICE):
            ow.connect = False
        ch_tasks.append(ch.set_permissions(guild.default_role, overwrite=ow,
                                           reason=f"Lockdown: {reason}"[:400]))
    for role in roles:
        key = str(role.id)
        if key not in role_state:
            role_state[key] = role.permissions.value
        role_tasks.append(role.edit(permissions=strip_dangerous(role.permissions),
                                    reason=f"Lockdown: {reason}"[:400]))
    return ch_tasks, role_tasks


async def _run(ch_tasks, role_tasks) -> dict:
    res_c = await asyncio.gather(*ch_tasks, return_exceptions=True)
    res_r = await asyncio.gather(*role_tasks, return_exceptions=True)
    return {
        "channels_locked": sum(1 for r in res_c if not isinstance(r, Exception)),
        "channels_total":  len(ch_tasks),
        "roles_stripped":  sum(1 for r in res_r if not isinstance(r, Exception)),
        "roles_total":     len(role_tasks),
    }


async def engage(bot, guild: discord.Guild, *, kind: str, reason: str, actor_id=None) -> dict:
    """Κλειδώνει ΟΛΑ τα κανάλια του server και αφαιρεί τα επικίνδυνα permissions
    από τα roles. Η αρχική κατάσταση σώζεται στη βάση ΠΡΙΝ γίνει οποιαδήποτε αλλαγή."""
    gid = str(guild.id)
    pause_backups(bot, gid)

    backup = bot.get_cog("Backup")
    if backup:
        try:
            await backup.create_backup(guild, kind="pre_lockdown", label=f"Before lockdown: {reason}"[:100])
        except Exception as e:
            print(f"[Lockdown] pre-lockdown backup failed: {e}")

    existing   = await Database.get_lockdown(gid)
    ch_state   = dict(existing["channels"]) if existing else {}
    role_state = dict(existing["roles"])    if existing else {}
    started    = existing["started"] if existing else None
    kind_saved = existing["kind"] if existing else kind

    channels = [c for c in guild.channels if isinstance(c, LOCK_TYPES)]
    roles    = _lockable_roles(guild)
    ch_tasks, role_tasks = _plan(guild, channels, roles, ch_state, role_state, reason)

    await Database.save_lockdown(gid, kind_saved, reason, actor_id, ch_state, role_state, started)
    return await _run(ch_tasks, role_tasks)


async def lock_new(bot, guild: discord.Guild, channels: list, roles: list, reason: str) -> dict:
    """Κλειδώνει μόνο καινούρια κανάλια/roles (π.χ. μετά από auto-restore) μέσα σε ενεργό lockdown."""
    gid      = str(guild.id)
    existing = await Database.get_lockdown(gid)
    if not existing:
        return {}
    ch_state, role_state = existing["channels"], existing["roles"]
    channels = [c for c in channels if isinstance(c, LOCK_TYPES)]
    roles    = [r for r in roles if is_dangerous(r.permissions) and r.position < guild.me.top_role.position]
    ch_tasks, role_tasks = _plan(guild, channels, roles, ch_state, role_state, reason)
    await Database.save_lockdown(gid, existing["kind"], existing["reason"], existing["actor_id"],
                                 ch_state, role_state, existing["started"])
    return await _run(ch_tasks, role_tasks)


async def restore(bot, guild: discord.Guild, *, actor=None) -> dict | None:
    """Ξεκλειδώνει τα κανάλια και επαναφέρει τα permissions των roles ακριβώς όπως ήταν."""
    gid   = str(guild.id)
    state = await Database.get_lockdown(gid)
    if not state:
        return None

    ch_tasks, role_tasks = [], []
    who = f" by {actor}" if actor else ""
    for cid, saved in state["channels"].items():
        ch = guild.get_channel(int(cid))
        if ch is None:
            continue
        ow = ch.overwrites_for(guild.default_role)
        for k in _OW_KEYS:
            setattr(ow, k, saved.get(k))
        ch_tasks.append(ch.set_permissions(
            guild.default_role, overwrite=None if ow.is_empty() else ow,
            reason=f"Lockdown lifted{who}"[:400]
        ))
    for rid, value in state["roles"].items():
        role = guild.get_role(int(rid))
        if role is None:
            continue
        role_tasks.append(role.edit(permissions=discord.Permissions(int(value)),
                                    reason=f"Lockdown lifted{who}"[:400]))

    stats = await _run(ch_tasks, role_tasks)
    await Database.clear_lockdown(gid)
    resume_backups(bot, gid)
    await Database.log_event(gid, "lockdown_lifted", {
        "user": str(actor) if actor else "system", "id": str(getattr(actor, "id", "")),
        "channels": stats["channels_locked"], "roles": stats["roles_stripped"],
    })
    return stats


# ── Notifications ───────────────────────────────────────────
async def owner_recipients(bot, guild: discord.Guild) -> list:
    ids = []
    if guild.owner_id:
        ids.append(guild.owner_id)
    for o in await Database.get_server_owners(str(guild.id)):
        if int(o) not in ids:
            ids.append(int(o))
    inst = getattr(bot, "installer_id", None)
    if inst and int(inst) not in ids:
        ids.append(int(inst))
    return ids


async def notify_owners(bot, guild: discord.Guild, title: str, lines: list) -> str:
    """DM στον owner· αν αποτύχει δοκιμάζει τους υπόλοιπους owners.
    Επιστρέφει γραμμή κατάστασης για το log."""
    embed = discord.Embed(title=title, description="\n".join(lines)[:3900], color=0x8B0000)
    embed.set_footer(text=f"{guild.name} • {guild.id}")
    tried = 0
    for uid in await owner_recipients(bot, guild):
        tried += 1
        try:
            user = bot.get_user(uid) or await bot.fetch_user(uid)
            await user.send(embed=embed)
            return f"DM sent to <@{uid}>" + ("" if tried == 1 else f" (after {tried - 1} failed)")
        except Exception:
            continue
    return "DM failed — no owner could be reached"


# ── Components ──────────────────────────────────────────────
def alert_components(guild_id, *, title: str, actor, reason: str, punish: dict | None,
                     lock: dict | None, dm_line: str, accent: int = 0x8B0000, src: str = "a") -> list:
    lines = [f"> {title}"]
    if actor is not None:
        tag = "  [BOT]" if getattr(actor, "bot", False) else ""
        lines.append(f"• Actor: {actor.mention}  ({actor.id}){tag}")
    lines.append(f"• Trigger: {reason}")

    if punish is not None:
        if punish.get("is_bot"):
            lines.append(f"• Bot action: roles stripped ({len(punish['stripped'])})  •  "
                         f"{'kicked' if punish['kicked'] else 'KICK FAILED'}")
        else:
            lines.append(f"• Roles stripped: {', '.join(punish['stripped']) or 'none'}")
            if punish["timeout"]:
                lines.append(f"• Timeout: {punish['timeout_hours']}h applied")
            elif punish["quarantined"]:
                lines.append("• Timeout failed — **Quarantine role applied**")
            else:
                lines.append("• Timeout FAILED and quarantine FAILED")
        if punish.get("warning"):
            lines.append(f"> WARNING: {punish['warning']}")

    if lock is None:
        lines.append("• Lockdown: engaging…")
    else:
        lines.append(
            f"• Lockdown: {lock['channels_locked']}/{lock['channels_total']} channels locked  •  "
            f"{lock['roles_stripped']}/{lock['roles_total']} roles stripped of dangerous perms"
        )
    lines.append(f"• {dm_line}")

    thumb = None
    if actor is not None and hasattr(actor, "display_avatar"):
        thumb = str(actor.display_avatar.url)
    return [ui.container([
        ui.header("\n".join(lines), thumb),
        ui.sep(),
        ui.row(ui.btn("Restore", f"lk_restore_{guild_id}_{src}", 3)),
    ], accent)]


def restored_components(by, stats: dict) -> list:
    return [ui.container([ui.text(
        "> Lockdown Lifted\n"
        f"• Channels restored: {stats['channels_locked']}/{stats['channels_total']}\n"
        f"• Roles restored: {stats['roles_stripped']}/{stats['roles_total']}\n"
        f"• By: {by.mention}"
    )], 0x57F287)]

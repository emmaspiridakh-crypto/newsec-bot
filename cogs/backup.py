import asyncio
import base64
import json
import time
import zlib

import discord
from discord.ext import commands, tasks
from discord import app_commands

from database import Database
from utils import lockdown, logger, ui
from utils.cv2_helper import update_cv2, respond_cv2, edit_original_cv2, no_access
from utils.security import strip_dangerous

KEEP = {"auto": 6, "pre_lockdown": 3, "manual": 10}
AUTO_DELAY    = 45     # δευτερόλεπτα ησυχίας πριν από auto backup
AUTO_MIN_GAP  = 120    # ελάχιστο διάστημα ανάμεσα σε δύο auto backups

CH_TEXT, CH_VOICE, CH_CATEGORY, CH_NEWS, CH_STAGE, CH_FORUM = 0, 2, 4, 5, 13, 15


# ── Serialization ──────────────────────────────────────────
def _overwrites(ch) -> list:
    out = []
    for target, ow in ch.overwrites.items():
        if isinstance(target, discord.Role):
            kind = "role"
        elif isinstance(target, discord.Member):
            kind = "member"
        else:
            continue
        allow, deny = ow.pair()
        out.append({"id": target.id, "type": kind, "allow": allow.value, "deny": deny.value})
    return out


def serialize(guild: discord.Guild) -> dict:
    roles = [{
        "id": r.id, "name": r.name, "color": r.color.value, "hoist": r.hoist,
        "mentionable": r.mentionable, "permissions": r.permissions.value,
        "position": r.position, "managed": r.managed, "default": r.is_default(),
    } for r in guild.roles]

    channels = []
    for ch in guild.channels:
        t = ch.type.value
        if t not in (CH_TEXT, CH_VOICE, CH_CATEGORY, CH_NEWS, CH_STAGE, CH_FORUM):
            continue
        d = {"id": ch.id, "name": ch.name, "type": t, "position": ch.position,
             "category_id": getattr(ch, "category_id", None), "overwrites": _overwrites(ch)}
        if t in (CH_TEXT, CH_NEWS, CH_FORUM, CH_STAGE):
            d["topic"] = getattr(ch, "topic", None)
        if t in (CH_TEXT, CH_NEWS, CH_FORUM):
            d["nsfw"]     = ch.nsfw
            d["slowmode"] = ch.slowmode_delay
        if t in (CH_VOICE, CH_STAGE):
            d["bitrate"]    = ch.bitrate
            d["user_limit"] = ch.user_limit
            d["rtc_region"] = str(ch.rtc_region) if ch.rtc_region else None
        channels.append(d)

    return {"version": 1, "guild_id": guild.id, "guild_name": guild.name,
            "roles": roles, "channels": channels}


def pack(data: dict) -> str:
    return base64.b64encode(zlib.compress(json.dumps(data).encode(), 6)).decode()


def unpack(blob: str) -> dict:
    return json.loads(zlib.decompress(base64.b64decode(blob.encode())).decode())


def summarize(data: dict) -> dict:
    return {"roles": sum(1 for r in data["roles"] if not r["default"]),
            "channels": len(data["channels"])}


# ── Restore ────────────────────────────────────────────────
def _build_overwrites(guild, items: list, role_map: dict) -> dict:
    out = {}
    for o in items:
        if o["type"] == "role":
            target = role_map.get(o["id"]) or guild.get_role(o["id"])
        else:
            target = guild.get_member(o["id"])
        if target is None:
            continue
        out[target] = discord.PermissionOverwrite.from_pair(
            discord.Permissions(o["allow"]), discord.Permissions(o["deny"]))
    return out


async def restore_structure(guild: discord.Guild, data: dict, *, apply_existing: bool) -> dict:
    """Ξαναδημιουργεί roles/κανάλια που λείπουν. Με apply_existing=True ξαναεφαρμόζει
    και permissions/overwrites σε όσα υπάρχουν ήδη. ΔΕΝ διαγράφει τίποτα."""
    locked   = await Database.get_lockdown(str(guild.id)) is not None
    top      = guild.me.top_role.position
    result   = {"roles": [], "channels": [], "new_roles": [], "new_channels": [],
                "reapplied": 0, "failed": 0}

    # ── Roles ──
    role_map: dict = {}
    used     = set()
    by_name  = {}
    for r in guild.roles:
        by_name.setdefault(r.name, r)
    new_positions = {}

    for rd in sorted(data["roles"], key=lambda x: x["position"]):
        if rd["default"]:
            role_map[rd["id"]] = guild.default_role
            if apply_existing and not locked and guild.default_role.permissions.value != rd["permissions"]:
                try:
                    await guild.default_role.edit(permissions=discord.Permissions(rd["permissions"]),
                                                  reason="Backup restore")
                    result["reapplied"] += 1
                except Exception:
                    result["failed"] += 1
            continue

        role = guild.get_role(rd["id"])
        if role is None and not rd["managed"]:
            cand = by_name.get(rd["name"])
            if cand is not None and cand.id not in used and not cand.managed and not cand.is_default():
                role = cand
        if role is not None:
            used.add(role.id)
            role_map[rd["id"]] = role
            if apply_existing and not locked and not role.managed and role.position < top:
                if (role.permissions.value != rd["permissions"] or role.color.value != rd["color"]
                        or role.hoist != rd["hoist"] or role.mentionable != rd["mentionable"]):
                    try:
                        await role.edit(permissions=discord.Permissions(rd["permissions"]),
                                        colour=discord.Colour(rd["color"]), hoist=rd["hoist"],
                                        mentionable=rd["mentionable"], reason="Backup restore")
                        result["reapplied"] += 1
                    except Exception:
                        result["failed"] += 1
            continue
        if rd["managed"]:
            continue

        perms = discord.Permissions(rd["permissions"])
        if locked:
            perms = strip_dangerous(perms)
        try:
            role = await guild.create_role(
                name=rd["name"], permissions=perms, colour=discord.Colour(rd["color"]),
                hoist=rd["hoist"], mentionable=rd["mentionable"], reason="Backup restore")
        except Exception as e:
            print(f"[Backup] create role '{rd['name']}' failed: {e}")
            result["failed"] += 1
            continue
        used.add(role.id)
        role_map[rd["id"]] = role
        result["roles"].append(role.name)
        result["new_roles"].append(role)
        new_positions[role] = max(1, min(rd["position"], top - 1))

    if new_positions:
        try:
            await guild.edit_role_positions(positions=new_positions, reason="Backup restore")
        except Exception as e:
            print(f"[Backup] role positions failed: {e}")

    # ── Channels ──
    chan_map: dict = {}
    used_ch = set()
    existing_by_key = {}
    for ch in guild.channels:
        existing_by_key.setdefault((ch.name, ch.type.value), []).append(ch)

    def find_existing(cd):
        ch = guild.get_channel(cd["id"])
        if ch is not None:
            return ch
        for cand in existing_by_key.get((cd["name"], cd["type"]), []):
            if cand.id not in used_ch:
                return cand
        return None

    ordered = sorted(data["channels"], key=lambda c: (c["type"] != CH_CATEGORY, c["position"]))
    for cd in ordered:
        ch = find_existing(cd)
        if ch is not None:
            used_ch.add(ch.id)
            chan_map[cd["id"]] = ch
            if apply_existing and not locked:
                desired = _build_overwrites(guild, cd["overwrites"], role_map)
                if dict(ch.overwrites) != desired:
                    try:
                        await ch.edit(overwrites=desired, reason="Backup restore")
                        result["reapplied"] += 1
                    except Exception:
                        result["failed"] += 1
            continue

        ow  = _build_overwrites(guild, cd["overwrites"], role_map)
        cat = chan_map.get(cd["category_id"]) if cd.get("category_id") else None
        t   = cd["type"]
        try:
            if t == CH_CATEGORY:
                ch = await guild.create_category(cd["name"], overwrites=ow, reason="Backup restore")
            elif t in (CH_TEXT, CH_NEWS):
                kw = dict(category=cat, overwrites=ow, topic=cd.get("topic") or None,
                          nsfw=cd.get("nsfw", False), slowmode_delay=cd.get("slowmode", 0) or 0,
                          reason="Backup restore")
                try:
                    ch = await guild.create_text_channel(cd["name"], news=(t == CH_NEWS), **kw)
                except discord.HTTPException:
                    if t != CH_NEWS:
                        raise
                    ch = await guild.create_text_channel(cd["name"], **kw)
            elif t == CH_VOICE:
                ch = await guild.create_voice_channel(
                    cd["name"], category=cat, overwrites=ow,
                    bitrate=min(cd.get("bitrate") or 64000, guild.bitrate_limit),
                    user_limit=cd.get("user_limit") or 0, reason="Backup restore")
            elif t == CH_STAGE:
                ch = await guild.create_stage_channel(cd["name"], category=cat, overwrites=ow,
                                                      reason="Backup restore")
            elif t == CH_FORUM:
                ch = await guild.create_forum(cd["name"], category=cat, overwrites=ow,
                                              topic=cd.get("topic") or None,
                                              nsfw=cd.get("nsfw", False), reason="Backup restore")
            else:
                continue
        except Exception as e:
            print(f"[Backup] create channel '{cd['name']}' failed: {e}")
            result["failed"] += 1
            continue
        used_ch.add(ch.id)
        chan_map[cd["id"]] = ch
        result["channels"].append(ch.name)
        result["new_channels"].append(ch)

    return result


# ── Cog ────────────────────────────────────────────────────
class Backup(commands.Cog):
    backup = app_commands.Group(name="backup", description="Server structure backups")

    def __init__(self, bot: commands.Bot):
        self.bot    = bot
        self._tasks = {}
        self._last  = {}
        if not hasattr(bot, "backup_pause"):
            bot.backup_pause = {}

    async def cog_load(self):
        self.hourly.start()

    async def cog_unload(self):
        self.hourly.cancel()
        for t in self._tasks.values():
            t.cancel()

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    # ── Core API ───────────────────────────────────────────
    async def create_backup(self, guild: discord.Guild, kind: str = "manual", label: str = None) -> int:
        data = serialize(guild)
        bid  = await Database.save_backup(str(guild.id), kind, label or kind, pack(data))
        self._last[str(guild.id)] = time.time()
        await Database.prune_backups(str(guild.id), kind, KEEP.get(kind, 6))
        return bid

    async def restore_missing(self, guild: discord.Guild, before_ts: int = None) -> dict | None:
        gid = str(guild.id)
        b = await Database.latest_backup(gid, before_ts=before_ts) or await Database.latest_backup(gid)
        if not b:
            return None
        res = await restore_structure(guild, unpack(b["data"]), apply_existing=False)
        res["backup_id"] = b["id"]
        return res

    async def _can_auto(self, guild: discord.Guild) -> bool:
        gid = str(guild.id)
        if not await Database.is_module_enabled(gid, "auto_backup"):
            return False
        if lockdown.backups_paused(self.bot, gid):
            return False
        return await Database.get_lockdown(gid) is None

    # ── Auto backups ───────────────────────────────────────
    @tasks.loop(minutes=60)
    async def hourly(self):
        for guild in list(self.bot.guilds):
            try:
                if await self._can_auto(guild):
                    await self.create_backup(guild, "auto", "Hourly")
            except Exception as e:
                print(f"[Backup] hourly failed for {guild.id}: {e}")
            await asyncio.sleep(0.5)

    @hourly.before_loop
    async def _before_hourly(self):
        await self.bot.wait_until_ready()

    def schedule(self, guild: discord.Guild):
        gid = str(guild.id)
        old = self._tasks.get(gid)
        if old and not old.done():
            old.cancel()
        self._tasks[gid] = asyncio.create_task(self._delayed(guild))

    async def _delayed(self, guild: discord.Guild):
        gid = str(guild.id)
        try:
            await asyncio.sleep(AUTO_DELAY)
            gap = AUTO_MIN_GAP - (time.time() - self._last.get(gid, 0))
            if gap > 0:
                await asyncio.sleep(gap)
            if await self._can_auto(guild):      # ελέγχεται ΞΑΝΑ τη στιγμή του backup
                await self.create_backup(guild, "auto", "Server changed")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Backup] auto backup failed for {gid}: {e}")

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):  self.schedule(channel.guild)
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):  self.schedule(channel.guild)
    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if (before.name, before.overwrites, before.position, getattr(before, "category_id", None)) != \
           (after.name, after.overwrites, after.position, getattr(after, "category_id", None)):
            self.schedule(after.guild)
    @commands.Cog.listener()
    async def on_guild_role_create(self, role):  self.schedule(role.guild)
    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):  self.schedule(role.guild)
    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        if before.permissions != after.permissions or before.name != after.name or before.position != after.position:
            self.schedule(after.guild)

    # ── Slash commands ─────────────────────────────────────
    @backup.command(name="create", description="Create a backup of roles and channels now")
    @app_commands.describe(label="Optional name")
    async def cmd_create(self, interaction: discord.Interaction, label: str = None):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        bid = await self.create_backup(interaction.guild, "manual", label or "Manual backup")
        info = summarize(serialize(interaction.guild))
        await logger.log(interaction.guild_id, "security", "Backup created", [
            f"• ID: #{bid}  •  Roles: {info['roles']}  •  Channels: {info['channels']}",
            f"• By: {interaction.user.mention}"])
        await edit_original_cv2(interaction, [ui.container([ui.text(
            f"> Backup Created\n• ID: **#{bid}**\n• Roles: {info['roles']}  •  Channels: {info['channels']}"
        )], 0x57F287)], ephemeral=True)

    @backup.command(name="list", description="List saved backups")
    async def cmd_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        items = await Database.list_backups(str(interaction.guild_id), 20)
        body = "\n".join(f"• **#{b['id']}**  `{b['kind']}`  <t:{b['created']}:R>  —  {b['label']}"
                         for b in items) or "No backups yet."
        await edit_original_cv2(interaction, [ui.container([
            ui.text("> Backups"), ui.sep(), ui.text(body)])], ephemeral=True)

    @backup.command(name="restore", description="Restore missing roles/channels from a backup")
    @app_commands.describe(backup_id="Backup ID (from /backup list)")
    async def cmd_restore(self, interaction: discord.Interaction, backup_id: int):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        gid = str(interaction.guild_id)
        if await Database.get_lockdown(gid):
            await edit_original_cv2(interaction, [ui.container([ui.text(
                "> Lockdown active\nLift the lockdown first (Restore button or `/unlock`), then restore the backup."
            )], 0xED4245)], ephemeral=True)
            return
        b = await Database.get_backup(gid, backup_id)
        if not b:
            await edit_original_cv2(interaction, [ui.container(
                [ui.text("> Not found\nNo such backup in this server.")], 0xED4245)], ephemeral=True)
            return
        info = {**summarize(unpack(b["data"])), "kind": b["kind"], "created": b["created"]}
        await edit_original_cv2(interaction, ui.restore_confirm(gid, b["id"], info), ephemeral=True)

    @backup.command(name="delete", description="Delete a backup")
    @app_commands.describe(backup_id="Backup ID")
    async def cmd_delete(self, interaction: discord.Interaction, backup_id: int):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        await Database.delete_backup(str(interaction.guild_id), backup_id)
        await edit_original_cv2(interaction, [ui.container(
            [ui.text(f"> Backup Deleted\n• ID: #{backup_id}")], 0xFEE75C)], ephemeral=True)

    # ── Confirm / cancel buttons ───────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if not (cid.startswith("bk_confirm_") or cid.startswith("bk_cancel_")):
            return
        if not interaction.guild_id:
            return
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await respond_cv2(interaction, [ui.container(
                [ui.text("> Access Denied\nServer Owners only.")], 0xED4245)], ephemeral=True)
            return

        if cid.startswith("bk_cancel_"):
            await update_cv2(interaction, [ui.container([ui.text("> Restore cancelled.")], 0x5865F2)])
            return

        gid, _, bid = cid[len("bk_confirm_"):].partition("_")
        if gid != str(interaction.guild_id) or not bid.isdigit():
            await respond_cv2(interaction, [ui.container(
                [ui.text("> Error\nThis button belongs to another server.")], 0xED4245)], ephemeral=True)
            return
        if await Database.get_lockdown(gid):
            await update_cv2(interaction, [ui.container([ui.text(
                "> Lockdown active\nLift the lockdown first, then restore.")], 0xED4245)])
            return
        b = await Database.get_backup(gid, int(bid))
        if not b:
            await update_cv2(interaction, [ui.container([ui.text("> Backup no longer exists.")], 0xED4245)])
            return

        await update_cv2(interaction, [ui.container([ui.text("> Restoring…\nThis can take a minute.")], 0xE67E22)])
        res = await restore_structure(interaction.guild, unpack(b["data"]), apply_existing=True)
        await logger.log(gid, "security", "Backup restored", [
            f"• Backup: #{b['id']}  •  By: {interaction.user.mention}",
            f"• Recreated roles: {len(res['roles'])}  •  channels: {len(res['channels'])}",
            f"• Re-applied permissions: {res['reapplied']}  •  Failed: {res['failed']}",
        ], event=("backup_restore", {"user": str(interaction.user), "id": str(interaction.user.id),
                                     "backup": b["id"]}))
        await edit_original_cv2(interaction, [ui.container([ui.text(
            "> Backup Restored\n"
            f"• Backup: **#{b['id']}**\n"
            f"• Recreated roles: {len(res['roles'])}  •  channels: {len(res['channels'])}\n"
            f"• Re-applied permissions/overwrites: {res['reapplied']}\n"
            f"• Failed: {res['failed']}"
        )], 0x57F287 if not res["failed"] else 0xE67E22)])


async def setup(bot: commands.Bot):
    await bot.add_cog(Backup(bot))

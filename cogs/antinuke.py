import asyncio
import datetime
import json

import discord
from discord.ext import commands
from discord import app_commands

from database import Database
from utils.cv2_helper import send_cv2, edit_original_cv2, no_access
from utils.tracker import ban_tracker, kick_tracker, channel_del_tracker, role_action_tracker

DANGEROUS_PERMS = (
    "administrator", "ban_members", "kick_members", "manage_guild",
    "manage_roles", "manage_channels", "manage_webhooks",
    "mention_everyone", "manage_messages",
)


class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    async def _log_id(self, guild_id: str) -> int | None:
        val = await Database.get_setting(guild_id, "log_channel_id")
        return int(val) if val else None

    # ── Punish + Lockdown ──────────────────────────────────
    async def _punish(self, guild: discord.Guild, moderator: discord.Member, reason: str):
        gid = str(guild.id)
        if await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return

        try:
            await moderator.timeout(datetime.timedelta(weeks=1), reason=reason)
        except Exception as e:
            print(f"[AntiNuke] timeout failed: {e}")

        tasks        = []
        locked_ids   = []
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                ow = channel.overwrites_for(guild.default_role)
                ow.send_messages = False
                ow.connect       = False
                tasks.append(channel.set_permissions(
                    guild.default_role, overwrite=ow, reason="Anti-Nuke Lockdown"
                ))
                locked_ids.append(channel.id)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        locked  = [cid for cid, r in zip(locked_ids, results) if not isinstance(r, Exception)]

        # Remember which channels we locked so /unlock only touches those.
        existing = json.loads(await Database.get_setting(gid, "lockdown_channels", "[]") or "[]")
        merged   = sorted(set(existing) | set(locked))
        await Database.set_setting(gid, "lockdown_channels", json.dumps(merged))
        await Database.set_setting(gid, "lockdown_active", "1")

        await Database.log_event(gid, "mass_action", {
            "user":   str(moderator),
            "id":     str(moderator.id),
            "reason": reason,
            "locked": len(locked)
        })

        cid = await self._log_id(gid)
        if cid:
            await send_cv2(cid, [{
                "type": 17,
                "accent_color": 0x8B0000,
                "components": [
                    {
                        "type": 9,
                        "components": [{"type": 10, "content": (
                            f"> ANTI-NUKE TRIGGERED\n"
                            f"• Moderator: {moderator.mention}  ({moderator.id})\n"
                            f"• Trigger: {reason}\n"
                            f"• Action: 1 week timeout\n"
                            f"• Lockdown: {len(locked)} / {len(tasks)} channels locked\n"
                            f"• Use `/unlock` to lift the lockdown once it's safe."
                        )}],
                        "accessory": {"type": 11, "media": {"url": str(moderator.display_avatar.url)}}
                    }
                ]
            }])

    # ── /unlock ─────────────────────────────────────────────
    @app_commands.command(name="unlock", description="Lift an active Anti-Nuke lockdown")
    async def unlock(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return

        locked_ids = json.loads(await Database.get_setting(gid, "lockdown_channels", "[]") or "[]")
        if not locked_ids:
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x5865F2, "components": [
                    {"type": 10, "content": "> No active lockdown\nNothing to unlock."}
                ]}
            ], ephemeral=True)
            return

        guild  = interaction.guild
        tasks  = []
        undone = []
        for cid in locked_ids:
            channel = guild.get_channel(cid)
            if channel is None:
                continue
            ow = channel.overwrites_for(guild.default_role)
            ow.send_messages = None
            ow.connect       = None
            tasks.append(channel.set_permissions(
                guild.default_role, overwrite=ow, reason=f"Lockdown lifted by {interaction.user}"
            ))
            undone.append(cid)

        results  = await asyncio.gather(*tasks, return_exceptions=True)
        unlocked = sum(1 for r in results if not isinstance(r, Exception))

        await Database.set_setting(gid, "lockdown_channels", "[]")
        await Database.set_setting(gid, "lockdown_active", "0")
        await Database.log_event(gid, "lockdown", {
            "user": str(interaction.user), "id": str(interaction.user.id),
            "unlocked": unlocked
        })

        await edit_original_cv2(interaction, [
            {"type": 17, "accent_color": 0x57F287, "components": [
                {"type": 10, "content": (
                    f"> Lockdown Lifted\n"
                    f"• Channels restored: {unlocked} / {len(undone)}\n"
                    f"• By: {interaction.user.mention}"
                )}
            ]}
        ], ephemeral=True)

    # ── Mass Ban ────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        gid = str(guild.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.ban)]
        except Exception:
            return
        if not entries:
            return
        moderator = entries[0].user
        if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return
        limit  = int(await Database.get_config(gid, "mass_action_limit",  "3"))
        window = int(await Database.get_config(gid, "mass_action_window", "10"))
        key    = f"ban_{guild.id}_{moderator.id}"
        if ban_tracker.add_and_check(key, limit, window):
            ban_tracker.reset(key)
            member = guild.get_member(moderator.id)
            if member:
                await self._punish(guild, member, f"Mass Ban ({limit}/{window}s)")

    # ── Mass Kick ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(limit=3, action=discord.AuditLogAction.kick)]
        except Exception:
            return
        for entry in entries:
            if (entry.target and entry.target.id == member.id and
                    (discord.utils.utcnow() - entry.created_at).total_seconds() < 5):
                moderator = entry.user
                if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
                    return
                limit  = int(await Database.get_config(gid, "mass_action_limit",  "3"))
                window = int(await Database.get_config(gid, "mass_action_window", "10"))
                key    = f"kick_{guild.id}_{moderator.id}"
                if kick_tracker.add_and_check(key, limit, window):
                    kick_tracker.reset(key)
                    mod_member = guild.get_member(moderator.id)
                    if mod_member:
                        await self._punish(guild, mod_member, f"Mass Kick ({limit}/{window}s)")
                break

    # ── Mass Channel Delete ─────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete)]
        except Exception:
            return
        if not entries:
            return
        moderator = entries[0].user
        if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return
        limit  = int(await Database.get_config(gid, "mass_action_limit",  "3"))
        window = int(await Database.get_config(gid, "mass_action_window", "10"))
        key    = f"ch_del_{guild.id}_{moderator.id}"
        if channel_del_tracker.add_and_check(key, limit, window):
            channel_del_tracker.reset(key)
            await Database.log_event(gid, "channel_delete", {"user": str(moderator), "channel": channel.name})
            member = guild.get_member(moderator.id)
            if member:
                await self._punish(guild, member, f"Mass Channel Delete ({limit}/{window}s)")

    # ── Mass Role Create / Delete ──────────────────────────
    async def _handle_role_spam(self, guild: discord.Guild, action_type, verb: str, key_prefix: str):
        gid = str(guild.id)
        if not await Database.is_module_enabled(gid, "role_action"):
            return
        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(limit=1, action=action_type)]
        except Exception:
            return
        if not entries:
            return
        moderator = entries[0].user
        if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return
        limit  = int(await Database.get_config(gid, "role_action_limit",  "3"))
        window = int(await Database.get_config(gid, "role_action_window", "10"))
        key    = f"{key_prefix}_{guild.id}_{moderator.id}"
        if role_action_tracker.add_and_check(key, limit, window):
            role_action_tracker.reset(key)
            member = guild.get_member(moderator.id)
            if member:
                await self._punish(guild, member, f"Mass Role {verb} ({limit}/{window}s)")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._handle_role_spam(
            role.guild, discord.AuditLogAction.role_create, "Create", "role_create"
        )

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._handle_role_spam(
            role.guild, discord.AuditLogAction.role_delete, "Delete", "role_delete"
        )

    # ── Role Permission Escalation ─────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "role_action"):
            return

        before_perms  = before.permissions
        after_perms   = after.permissions
        newly_granted = [
            p for p in DANGEROUS_PERMS
            if not getattr(before_perms, p) and getattr(after_perms, p)
        ]
        if not newly_granted:
            return

        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.role_update)]
        except Exception:
            return
        if not entries:
            return
        moderator = entries[0].user
        if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return

        # Revert the escalation itself, then punish — no threshold needed,
        # a single "administrator" grant is enough to nuke a server.
        try:
            await after.edit(permissions=before_perms, reason="Anti-Nuke: permission escalation reverted")
        except Exception as e:
            print(f"[AntiNuke] role revert failed: {e}")

        await Database.log_event(gid, "perm_escalation", {
            "user": str(moderator), "id": str(moderator.id),
            "role": after.name, "perms": ", ".join(newly_granted)
        })
        member = guild.get_member(moderator.id)
        if member:
            await self._punish(
                guild, member,
                f"Permission Escalation on '{after.name}' ({', '.join(newly_granted)})"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))

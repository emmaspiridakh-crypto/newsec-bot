import asyncio
import datetime

import discord
from discord.ext import commands

from database import Database
from utils.cv2_helper import send_cv2
from utils.tracker import ban_tracker, kick_tracker, channel_del_tracker, role_action_tracker

DANGEROUS_PERMS = (
    "administrator", "ban_members", "kick_members", "manage_guild",
    "manage_roles", "manage_channels", "manage_webhooks",
    "mention_everyone", "manage_messages",
), role_tracker


class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_id(self, guild_id: str) -> int | None:
        val = await Database.get_setting(guild_id, "log_channel_id")
        return int(val) if val else None

    async def _punish(self, guild: discord.Guild, moderator: discord.Member, reason: str):
        gid = str(guild.id)
        if await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return

        try:
            await moderator.timeout(datetime.timedelta(weeks=1), reason=reason)
        except Exception as e:
            print(f"[AntiNuke] timeout failed: {e}")

        tasks = []
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                ow = channel.overwrites_for(guild.default_role)
                ow.send_messages = False
                ow.connect       = False
                tasks.append(channel.set_permissions(
                    guild.default_role, overwrite=ow, reason="Anti-Nuke Lockdown"
                ))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        locked  = sum(1 for r in results if not isinstance(r, Exception))

        await Database.log_event(gid, "mass_action", {
            "user":   str(moderator),
            "id":     str(moderator.id),
            "reason": reason,
            "locked": locked
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
                            f"• Lockdown: {locked} / {len(tasks)} channels locked"
                        )}],
                        "accessory": {"type": 11, "media": {"url": str(moderator.display_avatar.url)}}
                    }
                ]
            }])

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


    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._track_role_action(role.guild, discord.AuditLogAction.role_delete, "role_del")

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._track_role_action(role.guild, discord.AuditLogAction.role_create, "role_create")

    async def _track_role_action(self, guild: discord.Guild, audit_action, key_prefix: str):
        gid = str(guild.id)
        if not await Database.is_module_enabled(gid, "role_abuse"):
            return
        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(limit=1, action=audit_action)]
        except Exception:
            return
        if not entries:
            return
        moderator = entries[0].user
        if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return
        limit  = int(await Database.get_config(gid, "mass_action_limit",  "3"))
        window = int(await Database.get_config(gid, "mass_action_window", "10"))
        key    = f"{key_prefix}_{guild.id}_{moderator.id}"
        if role_tracker.add_and_check(key, limit, window):
            role_tracker.reset(key)
            member = guild.get_member(moderator.id)
            if member:
                label = "Mass Role Delete" if key_prefix == "role_del" else "Mass Role Create"
                await self._punish(guild, member, f"{label} ({limit}/{window}s)")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "role_abuse"):
            return

        # Only care about permission escalation, not name/color changes
        gained_admin = (not before.permissions.administrator) and after.permissions.administrator
        dangerous = {"kick_members", "ban_members", "manage_channels", "manage_roles",
                     "manage_guild", "manage_webhooks", "mention_everyone"}
        before_perms = {p for p, v in before.permissions if v}
        after_perms  = {p for p, v in after.permissions if v}
        gained_dangerous = (after_perms - before_perms) & dangerous

        if not gained_admin and not gained_dangerous:
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

        member = guild.get_member(moderator.id)
        if not member:
            return

        reason = "Granted Administrator" if gained_admin else f"Granted: {', '.join(sorted(gained_dangerous))}"
        # No threshold — a single privilege escalation is punished immediately.
        try:
            await after.edit(permissions=before.permissions, reason="Anti-Nuke: permission escalation reverted")
        except Exception as e:
            print(f"[AntiNuke] role revert failed: {e}")

        await self._punish(guild, member, f"Role Escalation on '{after.name}' — {reason}")


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

        before_perms = before.permissions
        after_perms  = after.permissions
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

        # Permission escalation triggers immediately — no threshold needed,
        # a single "administrator" grant to a role is enough to nuke a server.
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

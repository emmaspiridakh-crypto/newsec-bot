import asyncio
import datetime
import json

import discord
from discord.ext import commands
from discord import app_commands

from database import Database
from utils.cv2_helper import send_cv2, edit_original_cv2, no_access
from utils.tracker import (
    ban_tracker, kick_tracker, channel_del_tracker, role_action_tracker,
    webhook_tracker,
)

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

        timeout_ok = True
        try:
            await moderator.timeout(datetime.timedelta(weeks=1), reason=reason)
        except Exception as e:
            timeout_ok = False
            print(f"[AntiNuke] timeout failed: {e}")

        # Strip any dangerous permissions the moderator holds via roles.
        # Timeout alone doesn't stop a compromised account from acting through
        # a webhook/bot it already set up, so pull the risky roles too.
        stripped_roles = []
        removable = [
            r for r in moderator.roles
            if r != guild.default_role
            and r < guild.me.top_role
            and any(getattr(r.permissions, p) for p in DANGEROUS_PERMS)
        ]
        if removable:
            try:
                await moderator.remove_roles(*removable, reason=reason)
                stripped_roles = [r.name for r in removable]
            except Exception as e:
                print(f"[AntiNuke] role strip failed: {e}")

        hierarchy_warning = not timeout_ok and not stripped_roles

        # Remember the ORIGINAL send_messages/connect values per channel before we
        # touch anything, so /unlock can restore the exact prior state instead of
        # just clearing the fields to "inherit" (which wipes any explicit
        # allow/deny the owner had set before the lockdown).
        original_perms = json.loads(await Database.get_setting(gid, "lockdown_original_perms", "{}") or "{}")

        tasks        = []
        locked_ids   = []
        for channel in guild.channels:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel)):
                ow = channel.overwrites_for(guild.default_role)
                cid_str = str(channel.id)
                # Only capture the original state the first time this channel gets
                # locked — if a lockdown is already active and triggers again, don't
                # let the already-locked (False/False) values overwrite the real original.
                if cid_str not in original_perms:
                    original_perms[cid_str] = {
                        "send_messages": ow.send_messages,
                        "connect":       ow.connect,
                    }
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
        await Database.set_setting(gid, "lockdown_original_perms", json.dumps(original_perms))
        await Database.set_setting(gid, "lockdown_active", "1")

        await Database.log_event(gid, "mass_action", {
            "user":   str(moderator),
            "id":     str(moderator.id),
            "reason": reason,
            "locked": len(locked)
        })

        role_line = (
            f"• Roles stripped: {', '.join(stripped_roles)}\n" if stripped_roles
            else "• Roles stripped: none (no dangerous role held below bot's position)\n"
        )
        warning_line = (
            "\n> ⚠ WARNING: timeout AND role strip both failed — this moderator likely "
            "outranks the bot in the role hierarchy. Manual intervention required.\n"
            if hierarchy_warning else ""
        )

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
                            f"• Action: {'1 week timeout' if timeout_ok else 'timeout FAILED'}\n"
                            f"{role_line}"
                            f"• Lockdown: {len(locked)} / {len(tasks)} channels locked\n"
                            f"• Use `/unlock` to lift the lockdown once it's safe."
                            f"{warning_line}"
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

        original_perms = json.loads(await Database.get_setting(gid, "lockdown_original_perms", "{}") or "{}")

        guild  = interaction.guild
        tasks  = []
        undone = []
        for cid in locked_ids:
            channel = guild.get_channel(cid)
            if channel is None:
                continue
            ow = channel.overwrites_for(guild.default_role)
            saved = original_perms.get(str(cid), {})
            # Restore the exact pre-lockdown values (which may themselves be
            # True/False/None) instead of blanket-clearing to "inherit".
            ow.send_messages = saved.get("send_messages", None)
            ow.connect       = saved.get("connect", None)
            tasks.append(channel.set_permissions(
                guild.default_role, overwrite=ow, reason=f"Lockdown lifted by {interaction.user}"
            ))
            undone.append(cid)

        results  = await asyncio.gather(*tasks, return_exceptions=True)
        unlocked = sum(1 for r in results if not isinstance(r, Exception))

        await Database.set_setting(gid, "lockdown_channels", "[]")
        await Database.set_setting(gid, "lockdown_original_perms", "{}")
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

    # ── Webhook Spam ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_webhook_update(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(limit=1, action=discord.AuditLogAction.webhook_create)]
        except Exception:
            return
        if not entries:
            return
        moderator = entries[0].user
        if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return
        limit  = int(await Database.get_config(gid, "mass_action_limit",  "3"))
        window = int(await Database.get_config(gid, "mass_action_window", "10"))
        key    = f"webhook_{guild.id}_{moderator.id}"
        if webhook_tracker.add_and_check(key, limit, window):
            webhook_tracker.reset(key)
            await Database.log_event(gid, "webhook_spam", {"user": str(moderator), "channel": channel.name})
            member = guild.get_member(moderator.id)
            if member:
                await self._punish(guild, member, f"Mass Webhook Create ({limit}/{window}s)")

    # ── Bot Added With Dangerous Permissions ───────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member.bot:
            return
        guild = member.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        if await Database.is_whitelist_bot(gid, str(member.id)):
            return

        has_dangerous = any(getattr(member.guild_permissions, p) for p in DANGEROUS_PERMS)
        if not has_dangerous:
            return

        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in guild.audit_logs(
                limit=3, action=discord.AuditLogAction.bot_add
            )]
        except Exception:
            entries = []

        adder = None
        for e in entries:
            if e.target and e.target.id == member.id:
                adder = e.user
                break
        if adder is None or adder.bot:
            return
        if await Database.is_server_owner(gid, str(adder.id), self.bot.installer_id):
            return

        await Database.log_event(gid, "dangerous_bot_add", {
            "user": str(adder), "id": str(adder.id), "bot": str(member)
        })

        # Strip the bot's dangerous roles immediately — don't wait on a threshold,
        # one privileged bot is enough to nuke the server.
        removable = [
            r for r in member.roles
            if r != guild.default_role and r < guild.me.top_role
            and any(getattr(r.permissions, p) for p in DANGEROUS_PERMS)
        ]
        try:
            if removable:
                await member.remove_roles(*removable, reason="Anti-Nuke: bot added with dangerous permissions")
        except Exception as e:
            print(f"[AntiNuke] dangerous bot role strip failed: {e}")

        cid = await self._log_id(gid)
        if cid:
            await send_cv2(cid, [{
                "type": 17,
                "accent_color": 0x8B0000,
                "components": [{
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> DANGEROUS BOT ADDED\n"
                        f"• Bot: {member.mention}  ({member.id})\n"
                        f"• Added by: {adder.mention}  ({adder.id})\n"
                        f"• Permissions stripped: {'yes' if removable else 'none found below bot rank'}\n"
                        f"• Review and use `/whitelist bot` if this was intentional."
                    )}],
                    "accessory": {"type": 11, "media": {"url": str(member.display_avatar.url)}}
                }]
            }])
        adder_member = guild.get_member(adder.id)
        if adder_member:
            await self._punish(guild, adder_member, f"Added bot '{member}' with dangerous permissions")

    # ── Server Vandalism (name/icon/vanity URL) ────────────
    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        gid = str(after.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return

        changes = []
        if before.name != after.name:
            changes.append(f"Name: `{before.name}` → `{after.name}`")
        if before.icon != after.icon:
            changes.append("Icon changed")
        if getattr(before, "vanity_url_code", None) != getattr(after, "vanity_url_code", None):
            changes.append("Vanity URL changed")
        if not changes:
            return

        await asyncio.sleep(0.5)
        try:
            entries = [e async for e in after.audit_logs(limit=1, action=discord.AuditLogAction.guild_update)]
        except Exception:
            return
        if not entries:
            return
        moderator = entries[0].user
        if moderator.bot or await Database.is_server_owner(gid, str(moderator.id), self.bot.installer_id):
            return

        await Database.log_event(gid, "guild_vandalism", {
            "user": str(moderator), "id": str(moderator.id), "changes": changes
        })

        cid = await self._log_id(gid)
        if cid:
            await send_cv2(cid, [{
                "type": 17,
                "accent_color": 0xE67E22,
                "components": [{
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> SERVER SETTINGS CHANGED\n"
                        f"• By: {moderator.mention}  ({moderator.id})\n"
                        f"• Changes:\n" + "\n".join(f"  - {c}" for c in changes)
                    )}],
                    "accessory": {"type": 11, "media": {"url": str(moderator.display_avatar.url)}}
                }]
            }])

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

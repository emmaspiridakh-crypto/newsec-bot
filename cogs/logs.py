"""Full logging σε ΕΝΑ κανάλι, 4 κατηγορίες: Members, Server, Security, Bot.
(Το Security τροφοδοτείται από τα cogs antinuke/automod/backup/panel.)"""
import discord
from discord.ext import commands

from database import Database
from utils import logger
from utils.audit import find_entry
from utils.security import dangerous_names

AA = discord.AuditLogAction

CHANNEL_KIND = {
    discord.ChannelType.text: "Text", discord.ChannelType.voice: "Voice",
    discord.ChannelType.category: "Category", discord.ChannelType.news: "Announcement",
    discord.ChannelType.stage_voice: "Stage", discord.ChannelType.forum: "Forum",
}


def _by(entry) -> str:
    if entry is None or entry.user is None:
        return "unknown"
    return f"{entry.user.mention}  ({entry.user.id})"


def _reason(entry) -> list:
    return [f"• Reason: {entry.reason}"] if entry is not None and entry.reason else []


def _kind(ch) -> str:
    return CHANNEL_KIND.get(ch.type, str(ch.type).split(".")[-1].title())


class Logs(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _enabled(self, guild, module: str) -> bool:
        gid = str(guild.id)
        return (await Database.is_module_enabled(gid, module)
                and bool(await Database.get_setting(gid, "log_channel_id")))

    # ══ MEMBERS ═══════════════════════════════════════════
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            # Αν το Bot Verify είναι ενεργό, η κάρτα επαλήθευσης καλύπτει ήδη το join
            gid = str(member.guild.id)
            verify = (await Database.is_module_enabled(gid, "bot_verify")
                      and not await Database.is_whitelist_bot(gid, str(member.id)))
            if not verify and await self._enabled(member.guild, "log_bot"):
                entry = await find_entry(member.guild, AA.bot_add, target_id=member.id, max_age=15)
                await logger.log(member.guild.id, "bot", "Bot added", [
                    f"• Bot: {member.mention}  ({member.id})", f"• Added by: {_by(entry)}",
                ], thumb=str(member.display_avatar.url))
            return
        if not await self._enabled(member.guild, "log_members"):
            return
        await logger.log(member.guild.id, "members", "Member joined", [
            f"• User: {member.mention}  ({member.id})",
            f"• Account created: <t:{int(member.created_at.timestamp())}:R>",
            f"• Member count: {member.guild.member_count}",
        ], thumb=str(member.display_avatar.url))

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        if member.bot:
            if await self._enabled(guild, "log_bot"):
                await logger.log(guild.id, "bot", "Bot removed", [
                    f"• Bot: **{member.name}**  ({member.id})"], thumb=str(member.display_avatar.url))
            return
        if not await self._enabled(guild, "log_members"):
            return
        entry = await find_entry(guild, AA.kick, target_id=member.id, max_age=6)
        roles = [r.mention for r in member.roles if not r.is_default()][:15]
        if entry is not None:
            await logger.log(guild.id, "members", "Member kicked", [
                f"• User: {member.mention}  ({member.id})", f"• By: {_by(entry)}", *_reason(entry),
            ], thumb=str(member.display_avatar.url), color=0xE67E22)
        else:
            await logger.log(guild.id, "members", "Member left", [
                f"• User: {member.mention}  ({member.id})",
                f"• Joined: <t:{int(member.joined_at.timestamp())}:R>" if member.joined_at else "• Joined: unknown",
                f"• Roles: {' '.join(roles) or 'none'}",
            ], thumb=str(member.display_avatar.url))

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        if not await self._enabled(guild, "log_members"):
            return
        entry = await find_entry(guild, AA.ban, target_id=user.id)
        await logger.log(guild.id, "members", "Member banned", [
            f"• User: {user.mention}  ({user.id})", f"• By: {_by(entry)}", *_reason(entry),
        ], thumb=str(user.display_avatar.url), color=0xED4245)

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        if not await self._enabled(guild, "log_members"):
            return
        entry = await find_entry(guild, AA.unban, target_id=user.id)
        await logger.log(guild.id, "members", "Member unbanned", [
            f"• User: {user.mention}  ({user.id})", f"• By: {_by(entry)}",
        ], thumb=str(user.display_avatar.url), color=0x57F287)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        guild = after.guild
        if not await self._enabled(guild, "log_members"):
            return
        thumb = str(after.display_avatar.url)

        if before.nick != after.nick:
            entry = await find_entry(guild, AA.member_update, target_id=after.id)
            await logger.log(guild.id, "members", "Nickname changed", [
                f"• User: {after.mention}  ({after.id})",
                f"• Before: `{before.nick or before.name}`", f"• After: `{after.nick or after.name}`",
                f"• By: {_by(entry)}",
            ], thumb=thumb)

        added   = [r for r in after.roles if r not in before.roles]
        removed = [r for r in before.roles if r not in after.roles]
        if added or removed:
            entry = await find_entry(guild, AA.member_role_update, target_id=after.id)
            lines = [f"• User: {after.mention}  ({after.id})"]
            if added:
                lines.append("• Added: " + " ".join(r.mention for r in added))
            if removed:
                lines.append("• Removed: " + " ".join(r.mention for r in removed))
            lines.append(f"• By: {_by(entry)}")
            risky = [r.name for r in added if dangerous_names(r.permissions)]
            if risky:
                lines.append(f"• Dangerous role(s) granted: {', '.join(risky)}")
            await logger.log(guild.id, "members", "Roles changed", lines, thumb=thumb,
                             color=0xE67E22 if risky else None)

        if before.timed_out_until != after.timed_out_until:
            entry = await find_entry(guild, AA.member_update, target_id=after.id)
            if after.timed_out_until:
                await logger.log(guild.id, "members", "Member timed out", [
                    f"• User: {after.mention}  ({after.id})",
                    f"• Until: <t:{int(after.timed_out_until.timestamp())}:F>",
                    f"• By: {_by(entry)}", *_reason(entry),
                ], thumb=thumb, color=0xE67E22)
            else:
                await logger.log(guild.id, "members", "Timeout removed", [
                    f"• User: {after.mention}  ({after.id})", f"• By: {_by(entry)}",
                ], thumb=thumb)

    # ══ SERVER ════════════════════════════════════════════
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        if not await self._enabled(channel.guild, "log_server"):
            return
        entry = await find_entry(channel.guild, AA.channel_create, target_id=channel.id)
        await logger.log(channel.guild.id, "server", "Channel created", [
            f"• {_kind(channel)}: **{channel.name}**  ({channel.id})", f"• By: {_by(entry)}"])

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if not await self._enabled(channel.guild, "log_server"):
            return
        entry = await find_entry(channel.guild, AA.channel_delete, target_id=channel.id)
        await logger.log(channel.guild.id, "server", "Channel deleted", [
            f"• {_kind(channel)}: **{channel.name}**  ({channel.id})", f"• By: {_by(entry)}"],
            color=0xED4245)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if not await self._enabled(after.guild, "log_server"):
            return
        changes = []
        if before.name != after.name:
            changes.append(f"• Name: `{before.name}` → `{after.name}`")
        if getattr(before, "topic", None) != getattr(after, "topic", None):
            changes.append("• Topic changed")
        if getattr(before, "nsfw", None) != getattr(after, "nsfw", None):
            changes.append(f"• NSFW: {getattr(before, 'nsfw', None)} → {getattr(after, 'nsfw', None)}")
        if getattr(before, "slowmode_delay", None) != getattr(after, "slowmode_delay", None):
            changes.append(f"• Slowmode: {before.slowmode_delay}s → {after.slowmode_delay}s")
        if before.overwrites != after.overwrites:
            changes.append("• Permission overwrites changed")
        if not changes:
            return
        entry = await find_entry(after.guild, AA.channel_update, target_id=after.id)
        if entry is None:
            entry = await find_entry(after.guild, AA.overwrite_update, target_id=after.id, delay=0)
        await logger.log(after.guild.id, "server", "Channel updated", [
            f"• Channel: {after.mention}  ({after.id})", *changes, f"• By: {_by(entry)}"])

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        if not await self._enabled(role.guild, "log_server"):
            return
        entry = await find_entry(role.guild, AA.role_create, target_id=role.id)
        await logger.log(role.guild.id, "server", "Role created", [
            f"• Role: {role.mention}  ({role.id})", f"• By: {_by(entry)}"])

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        if not await self._enabled(role.guild, "log_server"):
            return
        entry = await find_entry(role.guild, AA.role_delete, target_id=role.id)
        await logger.log(role.guild.id, "server", "Role deleted", [
            f"• Role: **{role.name}**  ({role.id})", f"• By: {_by(entry)}"], color=0xED4245)

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        if not await self._enabled(after.guild, "log_server"):
            return
        changes = []
        if before.name != after.name:
            changes.append(f"• Name: `{before.name}` → `{after.name}`")
        if before.color != after.color:
            changes.append(f"• Color: `{before.color}` → `{after.color}`")
        if before.hoist != after.hoist:
            changes.append(f"• Hoist: {before.hoist} → {after.hoist}")
        if before.mentionable != after.mentionable:
            changes.append(f"• Mentionable: {before.mentionable} → {after.mentionable}")
        if before.permissions != after.permissions:
            gained = [n for n, v in after.permissions if v and not getattr(before.permissions, n)]
            lost   = [n for n, v in before.permissions if v and not getattr(after.permissions, n)]
            if gained:
                changes.append("• Permissions added: " + ", ".join(gained))
            if lost:
                changes.append("• Permissions removed: " + ", ".join(lost))
        if not changes:
            return
        entry = await find_entry(after.guild, AA.role_update, target_id=after.id)
        await logger.log(after.guild.id, "server", "Role updated", [
            f"• Role: {after.mention}  ({after.id})", *changes, f"• By: {_by(entry)}"])

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        if not await self._enabled(after, "log_server"):
            return
        changes = []
        if before.name != after.name:
            changes.append(f"• Name: `{before.name}` → `{after.name}`")
        if before.icon != after.icon:
            changes.append("• Icon changed")
        if before.banner != after.banner:
            changes.append("• Banner changed")
        if before.owner_id != after.owner_id:
            changes.append(f"• Ownership: <@{before.owner_id}> → <@{after.owner_id}>")
        if before.verification_level != after.verification_level:
            changes.append(f"• Verification: {before.verification_level} → {after.verification_level}")
        if before.mfa_level != after.mfa_level:
            changes.append(f"• 2FA requirement: {before.mfa_level} → {after.mfa_level}")
        if before.explicit_content_filter != after.explicit_content_filter:
            changes.append("• Explicit content filter changed")
        if getattr(before, "vanity_url_code", None) != getattr(after, "vanity_url_code", None):
            changes.append("• Vanity URL changed")
        if not changes:
            return
        entry = await find_entry(after, AA.guild_update)
        await logger.log(after.id, "server", "Server updated", [*changes, f"• By: {_by(entry)}"])

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        if not await self._enabled(guild, "log_server"):
            return
        b, a = {e.id: e for e in before}, {e.id: e for e in after}
        for eid in a.keys() - b.keys():
            entry = await find_entry(guild, AA.emoji_create, target_id=eid)
            await logger.log(guild.id, "server", "Emoji created", [f"• {a[eid]} `{a[eid].name}`", f"• By: {_by(entry)}"])
        for eid in b.keys() - a.keys():
            entry = await find_entry(guild, AA.emoji_delete, target_id=eid)
            await logger.log(guild.id, "server", "Emoji deleted", [f"• `{b[eid].name}`", f"• By: {_by(entry)}"], color=0xED4245)

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after):
        if not await self._enabled(guild, "log_server"):
            return
        b, a = {s.id: s for s in before}, {s.id: s for s in after}
        for sid in a.keys() - b.keys():
            entry = await find_entry(guild, AA.sticker_create, target_id=sid)
            await logger.log(guild.id, "server", "Sticker created", [f"• `{a[sid].name}`", f"• By: {_by(entry)}"])
        for sid in b.keys() - a.keys():
            entry = await find_entry(guild, AA.sticker_delete, target_id=sid)
            await logger.log(guild.id, "server", "Sticker deleted", [f"• `{b[sid].name}`", f"• By: {_by(entry)}"], color=0xED4245)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        guild = invite.guild
        if guild is None or not await self._enabled(guild, "log_server"):
            return
        who = invite.inviter.mention if invite.inviter else "unknown"
        await logger.log(guild.id, "server", "Invite created", [
            f"• Code: `{invite.code}`  •  Channel: {getattr(invite.channel, 'mention', '?')}",
            f"• By: {who}", f"• Max uses: {invite.max_uses or '∞'}  •  Expires: "
            f"{f'<t:{int(invite.expires_at.timestamp())}:R>' if invite.expires_at else 'never'}"])

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite):
        guild = invite.guild
        if guild is None or not await self._enabled(guild, "log_server"):
            return
        await logger.log(guild.id, "server", "Invite deleted", [f"• Code: `{invite.code}`"])

    @commands.Cog.listener()
    async def on_webhook_update(self, channel):
        if not await self._enabled(channel.guild, "log_server"):
            return
        entry = None
        for act in (AA.webhook_create, AA.webhook_delete, AA.webhook_update):
            entry = await find_entry(channel.guild, act, max_age=6, delay=0.3)
            if entry is not None:
                title = {AA.webhook_create: "Webhook created", AA.webhook_delete: "Webhook deleted",
                         AA.webhook_update: "Webhook updated"}[act]
                break
        if entry is None:
            title = "Webhook changed"
        await logger.log(channel.guild.id, "server", title, [
            f"• Channel: {channel.mention}", f"• By: {_by(entry)}"])

    # ══ BOT ═══════════════════════════════════════════════
    @commands.Cog.listener()
    async def on_app_command_completion(self, interaction: discord.Interaction, command):
        if not interaction.guild_id or not await self._enabled(interaction.guild, "log_bot"):
            return
        opts = []
        for k, v in dict(interaction.namespace).items():
            opts.append(f"{k}={getattr(v, 'id', v)}")
        await logger.log(interaction.guild_id, "bot", "Command used", [
            f"• User: {interaction.user.mention}  ({interaction.user.id})",
            f"• Command: `/{command.qualified_name}`" + (f"  `{' '.join(opts)[:200]}`" if opts else ""),
            f"• Channel: <#{interaction.channel_id}>"], thumb=str(interaction.user.display_avatar.url))


async def setup(bot: commands.Bot):
    await bot.add_cog(Logs(bot))

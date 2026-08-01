import discord
from discord.ext import commands
from discord import app_commands
from database import Database
from utils.cv2_helper import edit_original_cv2, no_access


class WhitelistCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        wl  = app_commands.Group(name="whitelist",   description="Whitelist management")
        uwl = app_commands.Group(name="unwhitelist", description="Unwhitelist management")

        # ── /whitelist channel ────────────────────────────
        @wl.command(name="channel", description="Allow links in a channel for whitelist users")
        @app_commands.describe(channel="The channel")
        async def _wl_ch(interaction: discord.Interaction, channel: discord.TextChannel):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.add_whitelist_channel(gid, str(channel.id))
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x57F287, "components": [
                    {"type": 10, "content": (
                        f"> Channel Whitelisted\n"
                        f"• Channel: {channel.mention}\n"
                        f"• Whitelist users may send links here.\n"
                        f"• Set by: {interaction.user.mention}"
                    )}
                ]}
            ], ephemeral=True)

        # ── /whitelist role ───────────────────────────────
        @wl.command(name="role", description="Allow a role to send links anywhere, no channel whitelist needed")
        @app_commands.describe(role="The role")
        async def _wl_role(interaction: discord.Interaction, role: discord.Role):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.add_whitelist_role(gid, str(role.id))
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x57F287, "components": [
                    {"type": 10, "content": (
                        f"> Role Whitelisted\n"
                        f"• Role: {role.mention}\n"
                        f"• Members with this role may send links in any channel.\n"
                        f"• Set by: {interaction.user.mention}"
                    )}
                ]}
            ], ephemeral=True)

        # ── /whitelist bot ────────────────────────────────
        @wl.command(name="bot", description="Skip verification for a bot")
        @app_commands.describe(bot_id="Bot ID")
        async def _wl_bot(interaction: discord.Interaction, bot_id: str):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.add_whitelist_bot(gid, bot_id)
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x57F287, "components": [
                    {"type": 10, "content": (
                        f"> Bot Whitelisted\n"
                        f"• Bot ID: `{bot_id}`\n"
                        f"• Verification skipped on join.\n"
                        f"• Set by: {interaction.user.mention}"
                    )}
                ]}
            ], ephemeral=True)

        # ── /whitelist list ───────────────────────────────
        @wl.command(name="list", description="View all whitelisted entries")
        async def _wl_list(interaction: discord.Interaction):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return

            owners   = await Database.get_server_owners(gid)
            wl_users = await Database.get_whitelist_users(gid)
            wl_roles = await Database.get_whitelist_roles(gid)
            wl_bots  = await Database.get_whitelist_bots(gid)
            wl_chs   = await Database.get_whitelist_channels(gid)

            # installer πάντα εμφανίζεται ως owner
            installer = str(self.bot.installer_id) if self.bot.installer_id else None
            if installer and installer not in owners:
                owners = [installer] + owners

            def fu(ids):  return "\n".join(f"• <@{i}>"  for i in ids) or "• None"
            def fr(ids):  return "\n".join(f"• <@&{i}>" for i in ids) or "• None"
            def fb(ids):  return "\n".join(f"• `{i}`"   for i in ids) or "• None"
            def fch(ids): return "\n".join(f"• <#{i}>"  for i in ids) or "• None"

            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x5865F2, "components": [
                    {"type": 10, "content": "> Whitelist Overview"},
                    {"type": 14},
                    {"type": 10, "content": (
                        f"> Server Owners\n{fu(owners)}\n\n"
                        f"> Link Whitelist Users\n{fu(wl_users)}\n\n"
                        f"> Link Whitelist Roles\n{fr(wl_roles)}\n\n"
                        f"> Whitelisted Channels\n{fch(wl_chs)}\n\n"
                        f"> Whitelisted Bots\n{fb(wl_bots)}"
                    )}
                ]}
            ], ephemeral=True)

        # ── /unwhitelist channel ──────────────────────────
        @uwl.command(name="channel", description="Remove a channel from whitelist")
        @app_commands.describe(channel="The channel")
        async def _uwl_ch(interaction: discord.Interaction, channel: discord.TextChannel):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.remove_whitelist_channel(gid, str(channel.id))
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0xFEE75C, "components": [
                    {"type": 10, "content": (
                        f"> Channel Removed\n"
                        f"• Channel: {channel.mention}\n"
                        f"• Links no longer allowed.\n"
                        f"• By: {interaction.user.mention}"
                    )}
                ]}
            ], ephemeral=True)

        # ── /unwhitelist role ──────────────────────────────
        @uwl.command(name="role", description="Remove a role from whitelist")
        @app_commands.describe(role="The role")
        async def _uwl_role(interaction: discord.Interaction, role: discord.Role):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.remove_whitelist_role(gid, str(role.id))
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0xFEE75C, "components": [
                    {"type": 10, "content": (
                        f"> Role Removed\n"
                        f"• Role: {role.mention}\n"
                        f"• Links no longer allowed for this role outside whitelisted channels.\n"
                        f"• By: {interaction.user.mention}"
                    )}
                ]}
            ], ephemeral=True)

        # ── /unwhitelist bot ──────────────────────────────
        @uwl.command(name="bot", description="Remove a bot from whitelist")
        @app_commands.describe(bot_id="Bot ID")
        async def _uwl_bot(interaction: discord.Interaction, bot_id: str):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.remove_whitelist_bot(gid, bot_id)
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0xFEE75C, "components": [
                    {"type": 10, "content": (
                        f"> Bot Removed\n"
                        f"• Bot ID: `{bot_id}`\n"
                        f"• Will require verification on next join.\n"
                        f"• By: {interaction.user.mention}"
                    )}
                ]}
            ], ephemeral=True)

        self.whitelist_group   = wl
        self.unwhitelist_group = uwl

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    async def cog_load(self):
        self.bot.tree.add_command(self.whitelist_group)
        self.bot.tree.add_command(self.unwhitelist_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("whitelist")
        self.bot.tree.remove_command("unwhitelist")


async def setup(bot: commands.Bot):
    await bot.add_cog(WhitelistCmd(bot))

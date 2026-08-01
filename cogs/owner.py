import discord
from discord.ext import commands
from discord import app_commands
from database import Database
from utils.cv2_helper import edit_original_cv2, no_access


class Owner(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        add_grp = app_commands.Group(name="add",    description="Add to lists")
        rem_grp = app_commands.Group(name="remove", description="Remove from lists")

        # ── /add user ─────────────────────────────────────
        @add_grp.command(name="user", description="Add whitelist user")
        @app_commands.describe(member="The member")
        async def _add_user(interaction: discord.Interaction, member: discord.Member):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.add_whitelist_user(gid, str(member.id))
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x57F287, "components": [{
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> Whitelist User Added\n"
                        f"• User: {member.mention}  ({member.name})\n"
                        f"• Added by: {interaction.user.mention}\n"
                        f"• Permission: Links in whitelisted channels"
                    )}],
                    "accessory": {"type": 11, "media": {"url": str(member.display_avatar.url)}}
                }]}
            ], ephemeral=True)

        # ── /remove user ──────────────────────────────────
        @rem_grp.command(name="user", description="Remove whitelist user")
        @app_commands.describe(member="The member")
        async def _remove_user(interaction: discord.Interaction, member: discord.Member):
            await interaction.response.defer(ephemeral=True)
            gid = str(interaction.guild_id)
            if not await self._is_owner(interaction.guild_id, interaction.user.id):
                await no_access(interaction); return
            await Database.remove_whitelist_user(gid, str(member.id))
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0xFEE75C, "components": [{
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> Whitelist User Removed\n"
                        f"• User: {member.mention}\n"
                        f"• Removed by: {interaction.user.mention}"
                    )}],
                    "accessory": {"type": 11, "media": {"url": str(member.display_avatar.url)}}
                }]}
            ], ephemeral=True)

        self.add_group    = add_grp
        self.remove_group = rem_grp

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    async def cog_load(self):
        self.bot.tree.add_command(self.add_group)
        self.bot.tree.add_command(self.remove_group)

    async def cog_unload(self):
        self.bot.tree.remove_command("add")
        self.bot.tree.remove_command("remove")

    # ── /addserverowner ───────────────────────────────────
    @app_commands.command(name="addserverowner", description="Add Server Owner")
    @app_commands.describe(user_id="Discord User ID")
    async def addserverowner(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        await Database.add_server_owner(gid, user_id)
        try:
            user   = await self.bot.fetch_user(int(user_id))
            name   = f"{user.mention}  ({user.name})"
            avatar = str(user.display_avatar.url)
        except Exception:
            name, avatar = f"`{user_id}`", None
        section = {
            "type": 9,
            "components": [{"type": 10, "content": (
                f"> Server Owner Added\n"
                f"• User: {name}\n"
                f"• Added by: {interaction.user.mention}"
            )}]
        }
        if avatar:
            section["accessory"] = {"type": 11, "media": {"url": avatar}}
        await edit_original_cv2(interaction, [
            {"type": 17, "accent_color": 0x57F287, "components": [section]}
        ], ephemeral=True)

    # ── /removeserverowner ────────────────────────────────
    @app_commands.command(name="removeserverowner", description="Remove Server Owner")
    @app_commands.describe(user_id="Discord User ID")
    async def removeserverowner(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        if str(user_id) == str(self.bot.installer_id):
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0xED4245, "components": [
                    {"type": 10, "content": "> Not Allowed\nCannot remove the bot installer."}
                ]}
            ], ephemeral=True)
            return
        await Database.remove_server_owner(gid, user_id)
        await edit_original_cv2(interaction, [
            {"type": 17, "accent_color": 0xFEE75C, "components": [
                {"type": 10, "content": (
                    f"> Server Owner Removed\n"
                    f"• ID: `{user_id}`\n"
                    f"• Removed by: {interaction.user.mention}"
                )}
            ]}
        ], ephemeral=True)

    # ── /setlogchannel ────────────────────────────────────
    @app_commands.command(name="setlogchannel", description="Set the log channel")
    @app_commands.describe(channel="The log channel")
    async def setlogchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        await Database.set_setting(gid, "log_channel_id", str(channel.id))
        await edit_original_cv2(interaction, [
            {"type": 17, "accent_color": 0x57F287, "components": [
                {"type": 10, "content": (
                    f"> Log Channel Set\n"
                    f"• Channel: {channel.mention}\n"
                    f"• Set by: {interaction.user.mention}"
                )}
            ]}
        ], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Owner(bot))

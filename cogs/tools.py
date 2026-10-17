import discord
from discord.ext import commands
from discord import app_commands

from database import Database
from utils import health, logger, quarantine, ui
from utils.cv2_helper import edit_original_cv2, no_access


class Tools(commands.Cog):
    trusted = app_commands.Group(name="trusted", description="Trusted admins (exempt from Anti-Nuke)")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    # ── /trusted ───────────────────────────────────────────
    @trusted.command(name="add", description="Exempt a user/bot from Anti-Nuke")
    @app_commands.describe(user="Member or bot")
    async def trusted_add(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        await Database.add_trusted(str(interaction.guild_id), str(user.id))
        await edit_original_cv2(interaction, [ui.container([ui.text(
            f"> Trusted Admin Added\n• User: {user.mention}  ({user.id})\n"
            "• Anti-Nuke will no longer punish this account.")], 0x57F287)], ephemeral=True)

    @trusted.command(name="remove", description="Remove a trusted admin")
    @app_commands.describe(user="Member or bot")
    async def trusted_remove(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        await Database.remove_trusted(str(interaction.guild_id), str(user.id))
        await edit_original_cv2(interaction, [ui.container([ui.text(
            f"> Trusted Admin Removed\n• User: {user.mention}  ({user.id})")], 0xFEE75C)], ephemeral=True)

    @trusted.command(name="list", description="List trusted admins")
    async def trusted_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        ids = await Database.get_trusted(str(interaction.guild_id))
        await edit_original_cv2(interaction, [ui.container([
            ui.text("> Trusted Admins"), ui.sep(),
            ui.text("\n".join(f"• <@{i}>  ({i})" for i in ids) or "None")])], ephemeral=True)

    # ── /release ───────────────────────────────────────────
    @app_commands.command(name="release", description="Release a member from quarantine / punishment")
    @app_commands.describe(member="The punished member")
    async def release(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        res = await quarantine.release(interaction.guild, member)
        await logger.log(interaction.guild_id, "security", "Member released", [
            f"• User: {member.mention}  ({member.id})", f"• By: {interaction.user.mention}",
            f"• Roles restored: {len(res['restored'])}  •  Timeout cleared: {res['timeout_cleared']}"],
            event=("released", {"user": str(member), "id": str(member.id)}))
        await edit_original_cv2(interaction, [ui.container([ui.text(
            f"> Released\n• User: {member.mention}\n"
            f"• Quarantine role removed: {res['role_removed']}\n"
            f"• Timeout cleared: {res['timeout_cleared']}\n"
            f"• Roles restored: {', '.join(r.name for r in res['restored']) or 'none'}")], 0x57F287)],
            ephemeral=True)

    # ── /securitycheck ─────────────────────────────────────
    @app_commands.command(name="securitycheck", description="Health check of the security bot in this server")
    async def securitycheck(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        results = await health.run_checks(self.bot, interaction.guild)
        worst = 0xED4245 if any(l == health.FAIL for l, _ in results) else \
                0xE67E22 if any(l == health.WARN for l, _ in results) else 0x57F287
        await edit_original_cv2(interaction, [ui.container([
            ui.text("> Security Check"), ui.sep(), ui.text(health.render(results))], worst)],
            ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tools(bot))

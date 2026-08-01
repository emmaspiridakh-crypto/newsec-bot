import discord
from discord.ext import commands
from discord import app_commands
from database import Database
from utils.cv2_helper import edit_original_cv2, no_access

CONFIG_KEYS: dict[str, tuple[str, str, str]] = {
    "alt_age_days":       ("int",            "Account age in days for alt detection",      "30"),
    "alt_action":         ("choice:kick,log","Action on alt detection (kick / log)",       "kick"),
    "spam_threshold":     ("int",            "Messages before spam trigger",               "5"),
    "spam_window_secs":   ("int",            "Spam detection window in seconds",           "5"),
    "spam_timeout_mins":  ("int",            "Timeout duration for spam in minutes",       "10"),
    "link_timeout_mins":  ("int",            "Timeout duration for links in minutes",      "60"),
    "mass_action_limit":  ("int",            "Actions before anti-nuke triggers",          "3"),
    "mass_action_window": ("int",            "Anti-nuke detection window in seconds",      "10"),
    "role_action_limit":  ("int",            "Role create/delete actions before trigger",  "3"),
    "role_action_window": ("int",            "Role Guard detection window in seconds",     "10"),
}


class ConfigCmd(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    @app_commands.command(name="config", description="Security bot settings")
    @app_commands.describe(key="Setting key (blank = show all)", value="New value")
    async def config(self, interaction: discord.Interaction,
                     key: str = None, value: str = None):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return

        # Show all
        if not key:
            cfg   = await Database.get_all_config(gid)
            lines = []
            for k, (typ, desc, dflt) in CONFIG_KEYS.items():
                val = cfg.get(k, dflt)
                lines.append(f"• **{k}** = `{val}`\n  > {desc}")
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x5865F2, "components": [
                    {"type": 10, "content": "> Security Config"},
                    {"type": 14},
                    {"type": 10, "content": "\n\n".join(lines)},
                    {"type": 14},
                    {"type": 10, "content": "Usage: `/config <key> <value>`"}
                ]}
            ], ephemeral=True)
            return

        # Unknown key
        if key not in CONFIG_KEYS:
            valid = "  •  ".join(CONFIG_KEYS.keys())
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0xED4245, "components": [
                    {"type": 10, "content": f"> Unknown Key\n• `{key}` is not a valid setting.\n\n> Valid keys\n{valid}"}
                ]}
            ], ephemeral=True)
            return

        typ, desc, dflt = CONFIG_KEYS[key]

        # Show single key
        if not value:
            current = await Database.get_config(gid, key, dflt)
            await edit_original_cv2(interaction, [
                {"type": 17, "accent_color": 0x5865F2, "components": [
                    {"type": 10, "content": (
                        f"> {key}\n"
                        f"• Current value: `{current}`\n"
                        f"• Description: {desc}\n"
                        f"• Type: `{typ}`\n"
                        f"• Default: `{dflt}`"
                    )}
                ]}
            ], ephemeral=True)
            return

        # Validate int
        if typ == "int":
            try:
                int(value)
            except ValueError:
                await edit_original_cv2(interaction, [
                    {"type": 17, "accent_color": 0xED4245, "components": [
                        {"type": 10, "content": f"> Validation Error\n• `{key}` must be a number."}
                    ]}
                ], ephemeral=True)
                return
        # Validate choice
        elif typ.startswith("choice:"):
            choices = typ.split(":")[1].split(",")
            if value not in choices:
                await edit_original_cv2(interaction, [
                    {"type": 17, "accent_color": 0xED4245, "components": [
                        {"type": 10, "content": f"> Validation Error\n• `{key}` must be one of: {' / '.join(choices)}"}
                    ]}
                ], ephemeral=True)
                return

        await Database.set_config(gid, key, value)
        await edit_original_cv2(interaction, [
            {"type": 17, "accent_color": 0x57F287, "components": [
                {"type": 10, "content": f"> Config Updated\n• `{key}` — set to `{value}`"}
            ]}
        ], ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCmd(bot))

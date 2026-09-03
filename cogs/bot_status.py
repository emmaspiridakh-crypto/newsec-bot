from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import Database
from utils.cv2_helper import no_access, edit_original_cv2, update_cv2, respond_cv2

ACTIVITY_TYPES = {
    "playing": discord.ActivityType.playing,
    "watching": discord.ActivityType.watching,
    "listening": discord.ActivityType.listening,
    "competing": discord.ActivityType.competing,
}

STATUS_TYPES = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}

STATUS_NAMES_GR = {
    "online": "Online",
    "idle": "Idle",
    "dnd": "Do Not Disturb",
    "invisible": "Invisible",
}

DEFAULT_ROTATE_SECONDS = 15

GLOBAL_KEY = "bot_status_global"


def _default_data() -> dict:
    return {
        "type": "watching",
        "text": "server watching ",
        "presence": "online",
        "rotate": True,
        "interval": DEFAULT_ROTATE_SECONDS,

        "statuses": [
            {"type": "watching", "text": "〃 Created By: ! 3mma"},
            {"type": "watching", "text": "〃 Server Protected"},
        ],
        "update_override_active": False,
        "update_override_text": "",
    }


class UpdateTextModal(discord.ui.Modal, title="Update Status Text"):
    def __init__(self, cog: "BotStatus"):
        super().__init__(timeout=300)
        self.cog = cog
        self.text_input = discord.ui.TextInput(
            label="Live status text",
            placeholder="π.χ. Update in process...",
            default=cog._last_override_text,
            max_length=100,
            required=True,
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        text = str(self.text_input.value).strip()
        await self.cog.set_update_override(text)
        await edit_original_cv2(
            interaction,
            self.cog._build_update_panel(text, True),
            ephemeral=True,
        )


class BotStatus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._rotate_index = 0
        self._last_override_text = ""

    def cog_unload(self):
        if self.rotate_status_loop.is_running():
            self.rotate_status_loop.cancel()

    async def _is_owner(self, interaction: discord.Interaction) -> bool:
        gid = str(interaction.guild_id)
        return await Database.is_server_owner(gid, str(interaction.user.id), self.bot.installer_id)

    def _is_installer(self, interaction: discord.Interaction) -> bool:
        return str(interaction.user.id) == str(self.bot.installer_id)

    def _is_main_server(self, interaction: discord.Interaction) -> bool:
        main_id = getattr(self.bot, "main_server_id", None)
        return bool(main_id) and str(interaction.guild_id) == str(main_id)

    async def _get_data(self) -> dict:
        raw = await Database.get_setting(GLOBAL_KEY, "data", None)
        data = _default_data()
        if raw:
            try:
                data.update(json.loads(raw))
            except Exception:
                pass
        return data

    async def _save(self, data: dict):
        await Database.set_setting(GLOBAL_KEY, "data", json.dumps(data))

    def _build_activity(self, entry: dict) -> discord.Activity:
        activity_type = entry.get("type", "watching")
        text = entry.get("text", "server watching")
        if entry.get("dynamic") == "guild_count":
            text = f"{len(self.bot.guilds)} {text}"
        return discord.Activity(
            type=ACTIVITY_TYPES.get(activity_type, discord.ActivityType.watching),
            name=text,
        )

    async def _apply_presence(self, entry: dict | None = None):
        data = await self._get_data()
        status_key = data.get("presence", "online")
        discord_status = STATUS_TYPES.get(status_key, discord.Status.online)

        if data.get("update_override_active") and data.get("update_override_text"):
            entry = {"type": "watching", "text": data["update_override_text"]}
        elif entry is None:
            entry = {"type": data.get("type", "watching"), "text": data.get("text", "server watching")}

        activity = self._build_activity(entry)

        try:
            await self.bot.change_presence(status=discord_status, activity=activity)
        except discord.HTTPException:
            pass

    async def _apply_saved_status(self):
        data = await self._get_data()
        self._last_override_text = data.get("update_override_text", "")

        if data.get("update_override_active"):
            if self.rotate_status_loop.is_running():
                self.rotate_status_loop.cancel()
            await self._apply_presence()
            return

        await self._apply_presence()
        if data.get("rotate") and data.get("statuses"):
            interval = max(5, int(data.get("interval", DEFAULT_ROTATE_SECONDS)))
            if self.rotate_status_loop.seconds != interval:
                self.rotate_status_loop.change_interval(seconds=interval)
            if not self.rotate_status_loop.is_running():
                self.rotate_status_loop.start()
        else:
            if self.rotate_status_loop.is_running():
                self.rotate_status_loop.cancel()

    async def refresh_presence(self):
        await self._apply_saved_status()

    @commands.Cog.listener()
    async def on_ready(self):
        await self._apply_saved_status()

    @tasks.loop(seconds=DEFAULT_ROTATE_SECONDS)
    async def rotate_status_loop(self):
        data = await self._get_data()
        if data.get("update_override_active"):
            return
        statuses = data.get("statuses") or []
        if not statuses:
            return
        self._rotate_index = (self._rotate_index + 1) % len(statuses)
        entry = statuses[self._rotate_index]
        await self._apply_presence(entry)

    @rotate_status_loop.before_loop
    async def before_rotate(self):
        await self.bot.wait_until_ready()

    async def set_update_override(self, text: str):
        data = await self._get_data()
        data["update_override_active"] = True
        data["update_override_text"] = text
        await self._save(data)
        self._last_override_text = text
        await self._apply_saved_status()

    async def clear_update_override(self):
        data = await self._get_data()
        data["update_override_active"] = False
        await self._save(data)
        await self._apply_saved_status()

    def _build_update_panel(self, current_text: str, active: bool) -> list:
        state_line = (
            f"> Update Mode: ACTIVE\n• Live status: **{current_text}**"
            if active else
            "> Update Mode: inactive\nΤο bot δείχνει τα κανονικά live status."
        )
        return [{
            "type": 17,
            "accent_color": 0xE67E22 if active else 0x5865F2,
            "components": [
                {"type": 10, "content": "> Update Panel"},
                {"type": 14},
                {"type": 10, "content": state_line},
                {"type": 14},
                {
                    "type": 1,
                    "components": [
                        {"type": 2, "label": "Set / Edit Text", "style": 1, "custom_id": "upd_set"},
                        {"type": 2, "label": "Refresh",         "style": 2, "custom_id": "upd_refresh"},
                        {
                            "type": 2,
                            "label": "Finish Update",
                            "style": 4 if active else 2,
                            "custom_id": "upd_finish",
                            "disabled": not active,
                        },
                    ]
                }
            ]
        }]

    @app_commands.command(name="update", description="[Installer Only] Update-mode live status panel (main server only)")
    async def update_cmd(self, interaction: discord.Interaction):
        if not self._is_installer(interaction):
            await no_access(interaction, "Μόνο ο Installer μπορεί να χρησιμοποιήσει το /update."); return
        if not self._is_main_server(interaction):
            await no_access(interaction, "Η εντολή /update δουλεύει μόνο στον main server."); return

        await interaction.response.defer(ephemeral=True)
        data = await self._get_data()
        await edit_original_cv2(
            interaction,
            self._build_update_panel(data.get("update_override_text", ""), data.get("update_override_active", False)),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid not in ("upd_set", "upd_refresh", "upd_finish"):
            return

        if not self._is_installer(interaction):
            await respond_cv2(interaction, [
                {"type": 17, "accent_color": 0xED4245, "components": [
                    {"type": 10, "content": "> Access Denied\nΜόνο ο Bot Creator."}
                ]}
            ], ephemeral=True)
            return
        if not self._is_main_server(interaction):
            await respond_cv2(interaction, [
                {"type": 17, "accent_color": 0xED4245, "components": [
                    {"type": 10, "content": "> Access Denied\nΜόνο στον main server."}
                ]}
            ], ephemeral=True)
            return

        if cid == "upd_set":
            await interaction.response.send_modal(UpdateTextModal(self))
            return

        if cid == "upd_refresh":
            data = await self._get_data()
            await self._apply_saved_status()
            await update_cv2(
                interaction,
                self._build_update_panel(data.get("update_override_text", ""), data.get("update_override_active", False)),
            )
            return

        if cid == "upd_finish":
            await self.clear_update_override()
            await update_cv2(interaction, self._build_update_panel("", False))
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(BotStatus(bot))

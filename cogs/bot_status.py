from __future__ import annotations

import json

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database import Database
from utils.cv2_helper import no_access

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
    "idle": "Idle (Απασχολημένος)",
    "dnd": "Do Not Disturb",
    "invisible": "Invisible (Offline)",
}

DEFAULT_ROTATE_SECONDS = 20

# Global bot presence — not per-guild, so stored under a fixed pseudo-guild key.
GLOBAL_KEY = "bot_status_global"


def _default_data() -> dict:
    return {
        "type": "watching",
        "text": "το server",
        "presence": "online",
        "rotate": False,
        "interval": DEFAULT_ROTATE_SECONDS,
        "statuses": [],
    }


class BotStatus(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._rotate_index = 0

    def cog_unload(self):
        if self.rotate_status_loop.is_running():
            self.rotate_status_loop.cancel()

    async def _is_owner(self, interaction: discord.Interaction) -> bool:
        gid = str(interaction.guild_id)
        return await Database.is_server_owner(gid, str(interaction.user.id), self.bot.installer_id)

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
        text = entry.get("text", "το server")
        return discord.Activity(
            type=ACTIVITY_TYPES.get(activity_type, discord.ActivityType.watching),
            name=text,
        )

    async def _apply_presence(self, entry: dict | None = None):
        """Εφαρμόζει status+presence στο bot. Το bot συνεχίζει να δουλεύει κανονικά
        ανεξάρτητα από το ποιο presence (online/idle/dnd/invisible) δείχνει."""
        data = await self._get_data()
        status_key = data.get("presence", "online")
        discord_status = STATUS_TYPES.get(status_key, discord.Status.online)

        if entry is None:
            entry = {"type": data.get("type", "watching"), "text": data.get("text", "το server")}

        activity = self._build_activity(entry)

        try:
            await self.bot.change_presence(status=discord_status, activity=activity)
        except discord.HTTPException:
            pass

    async def _apply_saved_status(self):
        await self._apply_presence()
        data = await self._get_data()
        if data.get("rotate") and data.get("statuses"):
            interval = max(5, int(data.get("interval", DEFAULT_ROTATE_SECONDS)))
            if self.rotate_status_loop.seconds != interval:
                self.rotate_status_loop.change_interval(seconds=interval)
            if not self.rotate_status_loop.is_running():
                self.rotate_status_loop.start()
        else:
            if self.rotate_status_loop.is_running():
                self.rotate_status_loop.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        await self._apply_saved_status()

    @tasks.loop(seconds=DEFAULT_ROTATE_SECONDS)
    async def rotate_status_loop(self):
        data = await self._get_data()
        statuses = data.get("statuses") or []
        if not statuses:
            return
        self._rotate_index = (self._rotate_index + 1) % len(statuses)
        entry = statuses[self._rotate_index]
        await self._apply_presence(entry)

    @rotate_status_loop.before_loop
    async def before_rotate(self):
        await self.bot.wait_until_ready()

    @app_commands.command(name="setstatus", description="Αλλάζει το status του bot (σταθερό, απενεργοποιεί το rotation)")
    @app_commands.describe(type="Τύπος status", text="Το κείμενο που θα εμφανίζεται")
    @app_commands.choices(type=[
        app_commands.Choice(name="Playing", value="playing"),
        app_commands.Choice(name="Watching", value="watching"),
        app_commands.Choice(name="Listening", value="listening"),
        app_commands.Choice(name="Competing", value="competing"),
    ])
    async def setstatus(self, interaction: discord.Interaction, type: app_commands.Choice[str], text: str):
        if not await self._is_owner(interaction):
            await interaction.response.send_message(" Μόνο ο Owner ή ο Installer μπορεί να αλλάξει το status του bot.", ephemeral=True)
            return

        data = await self._get_data()
        data["type"] = type.value
        data["text"] = text
        data["rotate"] = False
        await self._save(data)
        await self._apply_saved_status()

        await interaction.response.send_message(
            f"Το status ενημερώθηκε: **{type.name} {text}**", ephemeral=True
        )

    @app_commands.command(name="setpresence", description="Αλλάζει την εμφάνιση του bot (online/idle/dnd/invisible)")
    @app_commands.describe(status="Η κατάσταση εμφάνισης")
    @app_commands.choices(status=[
        app_commands.Choice(name="Online", value="online"),
        app_commands.Choice(name="Idle", value="idle"),
        app_commands.Choice(name="Do Not Disturb", value="dnd"),
        app_commands.Choice(name="Invisible (Offline)", value="invisible"),
    ])
    async def setpresence(self, interaction: discord.Interaction, status: app_commands.Choice[str]):
        if not await self._is_owner(interaction):
            await interaction.response.send_message(" Μόνο ο Owner ή ο Installer μπορεί να αλλάξει την εμφάνιση του bot.", ephemeral=True)
            return

        data = await self._get_data()
        data["presence"] = status.value
        await self._save(data)
        await self._apply_saved_status()

        await interaction.response.send_message(
            f"Η εμφάνιση του bot άλλαξε σε **{STATUS_NAMES_GR.get(status.value, status.name)}**. "
            f"Το bot συνεχίζει να λειτουργεί κανονικά (moderation/anti-nuke ενεργά).",
            ephemeral=True,
        )

    @app_commands.command(name="addstatus", description="Προσθέτει ένα status στη λίστα εναλλαγής (rotation)")
    @app_commands.describe(type="Τύπος status", text="Το κείμενο που θα εμφανίζεται")
    @app_commands.choices(type=[
        app_commands.Choice(name="Playing", value="playing"),
        app_commands.Choice(name="Watching", value="watching"),
        app_commands.Choice(name="Listening", value="listening"),
        app_commands.Choice(name="Competing", value="competing"),
    ])
    async def addstatus(self, interaction: discord.Interaction, type: app_commands.Choice[str], text: str):
        if not await self._is_owner(interaction):
            await interaction.response.send_message(" Μόνο ο Owner ή ο Installer μπορεί να το κάνει αυτό.", ephemeral=True)
            return

        data = await self._get_data()
        statuses = data.get("statuses") or []
        statuses.append({"type": type.value, "text": text})
        data["statuses"] = statuses
        await self._save(data)

        await interaction.response.send_message(
            f"Προστέθηκε στη λίστα: **{type.name} {text}** (τώρα έχει {len(statuses)} statuses).\n"
            f"Χρησιμοποίησε `/togglerotation` για να ενεργοποιήσεις την αυτόματη εναλλαγή.",
            ephemeral=True,
        )

    @app_commands.command(name="removestatus", description="Αφαιρεί ένα status από τη λίστα εναλλαγής")
    @app_commands.describe(index="Ο αριθμός του status (δες με /liststatus)")
    async def removestatus(self, interaction: discord.Interaction, index: int):
        if not await self._is_owner(interaction):
            await interaction.response.send_message(" Μόνο ο Owner ή ο Installer μπορεί να το κάνει αυτό.", ephemeral=True)
            return

        data = await self._get_data()
        statuses = data.get("statuses") or []
        if index < 1 or index > len(statuses):
            await interaction.response.send_message("Μη έγκυρος αριθμός.", ephemeral=True)
            return

        removed = statuses.pop(index - 1)
        data["statuses"] = statuses
        await self._save(data)

        await interaction.response.send_message(
            f"Αφαιρέθηκε: **{removed.get('text')}**", ephemeral=True
        )

    @app_commands.command(name="liststatus", description="Δείχνει τη λίστα των statuses εναλλαγής")
    async def liststatus(self, interaction: discord.Interaction):
        data = await self._get_data()
        statuses = data.get("statuses") or []
        if not statuses:
            await interaction.response.send_message("Δεν υπάρχουν statuses στη λίστα.", ephemeral=True)
            return

        lines = [f"{i+1}. **{s.get('type')}** — {s.get('text')}" for i, s in enumerate(statuses)]
        state = "ενεργό ✅" if data.get("rotate") else "ανενεργό ❌"
        await interaction.response.send_message(
            f"**Rotation:** {state} | **Interval:** {data.get('interval')}s | **Presence:** {STATUS_NAMES_GR.get(data.get('presence'), data.get('presence'))}\n\n"
            + "\n".join(lines),
            ephemeral=True,
        )

    @app_commands.command(name="togglerotation", description="Ενεργοποιεί/απενεργοποιεί την αυτόματη εναλλαγή status")
    @app_commands.describe(interval="Δευτερόλεπτα ανάμεσα σε κάθε αλλαγή (προαιρετικό)")
    async def togglerotation(self, interaction: discord.Interaction, interval: int | None = None):
        if not await self._is_owner(interaction):
            await interaction.response.send_message(" Μόνο ο Owner ή ο Installer μπορεί να το κάνει αυτό.", ephemeral=True)
            return

        data = await self._get_data()
        if not data.get("statuses"):
            await interaction.response.send_message(
                "Δεν έχεις προσθέσει statuses. Χρησιμοποίησε πρώτα `/addstatus`.", ephemeral=True
            )
            return

        data["rotate"] = not data.get("rotate", False)
        if interval:
            data["interval"] = max(5, interval)
        await self._save(data)
        await self._apply_saved_status()

        state = "ενεργοποιήθηκε ✅" if data["rotate"] else "απενεργοποιήθηκε ❌"
        await interaction.response.send_message(
            f"Το rotation {state} (κάθε {data.get('interval')}s).", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BotStatus(bot))

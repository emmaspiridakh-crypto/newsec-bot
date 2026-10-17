import discord
from discord.ext import commands, tasks
import asyncio
import os
import traceback
from dotenv import load_dotenv

load_dotenv()   # ΠΡΙΝ από τα imports που διαβάζουν env (π.χ. TOKEN στο cv2_helper)

from database import Database
from keep_alive import keep_alive
from utils.cv2_helper import close_session
from utils.tracker import ActionTracker

TOKEN          = os.getenv("TOKEN")
INSTALLER_ID   = os.getenv("INSTALLER_ID")
MAIN_SERVER_ID = os.getenv("MAIN_SERVER_ID")

if not TOKEN:
    print("❌ TOKEN missing from .env"); exit(1)
if not INSTALLER_ID:
    print("❌ INSTALLER_ID missing from .env"); exit(1)
if not MAIN_SERVER_ID:
    print("⚠ MAIN_SERVER_ID missing from .env — /allservers and /update will be disabled everywhere.")

intents = discord.Intents.all()

class SecurityBot(commands.Bot):
    async def setup_hook(self):
        # Το sync γίνεται ΜΙΑ φορά στην εκκίνηση (όχι σε κάθε reconnect/on_ready → rate limits)
        await self.tree.sync()
        sweep_trackers.start()


bot = SecurityBot(command_prefix="§", intents=intents)
bot.installer_id   = INSTALLER_ID
bot.main_server_id = MAIN_SERVER_ID
bot.backup_pause   = {}
Database.bot       = bot      # ώστε ο πραγματικός owner του server να αναγνωρίζεται ως Server Owner


@tasks.loop(minutes=10)
async def sweep_trackers():
    """Καθαρίζει τα παλιά κλειδιά των trackers (αποτρέπει memory leak)."""
    ActionTracker.sweep_all(600)

COGS = [
    "cogs.panel",
    "cogs.owner",
    "cogs.whitelist_cmd",
    "cogs.config_cmd",
    "cogs.antinuke",
    "cogs.automod",
    "cogs.logs",
    "cogs.backup",
    "cogs.tools",
    "cogs.bot_status",
]

@bot.event
async def on_ready():
    print(f"✅ {bot.user} online | Installer: {INSTALLER_ID} | Servers: {len(bot.guilds)}")

@bot.event
async def on_guild_join(guild: discord.Guild):
    print(f"[+] Joined: {guild.name} ({guild.id}) — Total: {len(bot.guilds)}")
    status_cog = bot.get_cog("BotStatus")
    if status_cog:
        await status_cog.refresh_presence()

@bot.event
async def on_guild_remove(guild: discord.Guild):
    print(f"[-] Left: {guild.name} ({guild.id}) — Total: {len(bot.guilds)}")
    status_cog = bot.get_cog("BotStatus")
    if status_cog:
        await status_cog.refresh_presence()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    cmd_name = interaction.command.name if interaction.command else "unknown"
    print(f"[ERROR] /{cmd_name}: {error}")
    traceback.print_exc()
    try:
        from utils.cv2_helper import edit_original_cv2, respond_cv2
        msg = [{"type": 17, "accent_color": 0xED4245, "components": [
            {"type": 10, "content": f"> Error\n`{error}`"}
        ]}]
        if interaction.response.is_done():
            await edit_original_cv2(interaction, msg, ephemeral=True)
        else:
            await respond_cv2(interaction, msg, ephemeral=True)
    except Exception as e:
        print(f"[ERROR HANDLER FAILED] {e}")

async def main():
    async with bot:
        await Database.init()
        try:
            for cog in COGS:
                await bot.load_extension(cog)
            keep_alive()
            await bot.start(TOKEN)
        finally:
            await Database.close()
            await close_session()

asyncio.run(main())

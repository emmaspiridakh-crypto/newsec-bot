import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from database import Database
from utils.cv2_helper import respond_cv2, update_cv2, send_cv2, no_access, edit_original_cv2


# ── Helpers ────────────────────────────────────────────────────────────────────

def _on(key, disabled):     return key not in disabled
def _status(key, disabled): return "[ON]"  if _on(key, disabled) else "[OFF]"
def _style(key, disabled):  return 3       if _on(key, disabled) else 4


# ── Panel ──────────────────────────────────────────────────────────────────────

def build_panel(cfg: dict, disabled: list, guild_icon: str | None) -> list:
    alt_action = cfg.get("alt_action",        "kick")
    alt_days   = cfg.get("alt_age_days",      "30")
    spam_thr   = cfg.get("spam_threshold",    "5")
    spam_win   = cfg.get("spam_window_secs",  "5")
    spam_to    = cfg.get("spam_timeout_mins", "10")
    link_to    = cfg.get("link_timeout_mins", "60")
    mass_lim   = cfg.get("mass_action_limit", "3")
    mass_win   = cfg.get("mass_action_window","10")
    role_lim   = cfg.get("role_action_limit", "3")
    role_win   = cfg.get("role_action_window","10")

    header = {
        "type": 9,
        "components": [{"type": 10, "content": "> Security Control Panel\nManage settings."}],
    }
    if guild_icon:
        header["accessory"] = {"type": 11, "media": {"url": guild_icon}}

    def section(key, name, detail, btn_id):
        return {
            "type": 9,
            "components": [{"type": 10, "content": (
                f"**{name}**  {_status(key, disabled)}\n"
                f"> {detail}"
            )}],
            "accessory": {
                "type": 2,
                "label": name,
                "style": _style(key, disabled),
                "custom_id": btn_id
            }
        }

    return [{
        "type": 17,
        "accent_color": 0x8B0000,
        "components": [
            header,
            {"type": 14},
            section("alt",         "ALT Detection", f"{alt_days} days  •  Action: {alt_action.capitalize()}",      "sec_toggle_alt"),
            section("link",        "Link Filter",   f"Timeout: {link_to} min",                                      "sec_toggle_link"),
            section("token",       "Token Filter",  "Auto-delete Discord tokens",                                   "sec_toggle_token"),
            section("spam",        "Spam Filter",   f"{spam_thr} msgs / {spam_win}s  •  {spam_to} min timeout",   "sec_toggle_spam"),
            section("mass_action", "Mass Action",   f"{mass_lim} actions / {mass_win}s  •  Auto-lockdown",        "sec_toggle_mass"),
            section("role_action", "Role Guard",    f"{role_lim} actions / {role_win}s  •  Perm-escalation block", "sec_toggle_role"),
            section("bot_verify",  "Bot Verify",    "Zero permissions until accepted",                              "sec_toggle_botverify"),
            {"type": 14},
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "label": f"Action: {alt_action.capitalize()}",
                        "style": 3 if alt_action == "kick" else 2,
                        "custom_id": "sec_toggle_altkick"
                    },
                    {"type": 2, "label": "Status",      "style": 1, "custom_id": "sec_status"},
                    {"type": 2, "label": "Recent Logs", "style": 2, "custom_id": "sec_logs_btn"}
                ]
            }
        ]
    }]


# ── Status ─────────────────────────────────────────────────────────────────────

def build_status(cfg: dict, disabled: list, wl_u: int, wl_b: int, wl_c: int,
                 guild_icon: str | None) -> list:
    def s(k): return "[ON]" if k not in disabled else "[OFF]"

    header = {
        "type": 9,
        "components": [{"type": 10, "content": "> Security Status"}],
    }
    if guild_icon:
        header["accessory"] = {"type": 11, "media": {"url": guild_icon}}

    return [{
        "type": 17,
        "accent_color": 0x5865F2,
        "components": [
            header,
            {"type": 14},
            {"type": 10, "content": (
                f"• ALT Detection    {s('alt')}\n"
                f"• Link Filter      {s('link')}\n"
                f"• Token Filter     {s('token')}\n"
                f"• Spam Filter      {s('spam')}\n"
                f"• Mass Action      {s('mass_action')}\n"
                f"• Role Guard       {s('role_action')}\n"
                f"• Bot Verify       {s('bot_verify')}"
            )},
            {"type": 14},
            {"type": 10, "content": (
                f"> Configuration\n"
                f"• Alt threshold: {cfg.get('alt_age_days','30')} days  •  Action: {cfg.get('alt_action','kick').capitalize()}\n"
                f"• Spam: {cfg.get('spam_threshold','5')} msgs / {cfg.get('spam_window_secs','5')}s  •  {cfg.get('spam_timeout_mins','10')} min timeout\n"
                f"• Link timeout: {cfg.get('link_timeout_mins','60')} min\n"
                f"• Mass action: {cfg.get('mass_action_limit','3')} actions / {cfg.get('mass_action_window','10')}s\n"
                f"• Role guard: {cfg.get('role_action_limit','3')} actions / {cfg.get('role_action_window','10')}s\n"
                f"• Whitelist — Users: {wl_u}  •  Bots: {wl_b}  •  Channels: {wl_c}"
            )}
        ]
    }]


# ── Logs ───────────────────────────────────────────────────────────────────────

def build_logs(events: list, guild_icon: str | None) -> list:
    TYPE_LABEL = {
        "token_detected": "Token Detected",
        "link_detected":  "Link Detected",
        "spam_detected":  "Spam Detected",
        "alt_detected":   "Alt Detected",
        "bot_join":       "Bot Join",
        "mass_action":    "Mass Action",
        "channel_delete": "Channel Delete",
        "perm_escalation":"Permission Escalation",
        "lockdown":       "Lockdown",
    }

    if not events:
        body = "No security events logged yet."
    else:
        lines = []
        for e in events:
            who   = e["data"].get("user") or e["data"].get("bot") or "unknown"
            ts    = e["timestamp"] // 1000
            label = TYPE_LABEL.get(e["type"], e["type"])
            lines.append(f"• **{label}**  —  {who}  <t:{ts}:R>")
        body = "\n".join(lines)

    header = {
        "type": 9,
        "components": [{"type": 10, "content": "> Recent Security Events"}],
    }
    if guild_icon:
        header["accessory"] = {"type": 11, "media": {"url": guild_icon}}

    return [{"type": 17, "accent_color": 0x8B0000,
             "components": [header, {"type": 14}, {"type": 10, "content": body}]}]


# ── Help ───────────────────────────────────────────────────────────────────────

def build_help(is_owner: bool) -> list:
    owner_block = (
        "> Owner Commands\n"
        "• /addserverowner <id>  —  Add Server Owner\n"
        "• /removeserverowner <id>  —  Remove Server Owner\n"
        "• /add user @user  —  Add whitelist user\n"
        "• /remove user @user  —  Remove whitelist user\n\n"
    ) if is_owner else ""

    body = (
        f"{owner_block}"
        "> Config  (Owner only)\n"
        "• /config  —  View all settings\n"
        "• /config <key> <value>  —  Update a setting\n\n"
        "> Whitelist  (Owner only)\n"
        "• /whitelist channel #ch  —  Allow links in channel\n"
        "• /whitelist bot <id>  —  Skip bot verification\n"
        "• /whitelist list  —  View all whitelisted\n"
        "• /unwhitelist channel #ch  —  Remove channel\n"
        "• /unwhitelist bot <id>  —  Remove bot\n\n"
        "> Panel and Logs  (Owner only)\n"
        "• /panel  —  Security Control Panel\n"
        "• /status  —  Module overview\n"
        "• /logs [amount]  —  Recent events\n"
        "• /setlogchannel #channel  —  Set log channel\n"
        "• /sechelp  —  This message"
    )

    return [{"type": 17, "accent_color": 0x5865F2, "components": [
        {"type": 10, "content": "> Security Bot — Commands"},
        {"type": 14},
        {"type": 10, "content": body}
    ]}]


# ── Cog ────────────────────────────────────────────────────────────────────────

class Panel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    def _icon(self, guild: discord.Guild) -> str | None:
        return str(guild.icon.url) if guild and guild.icon else None

    # ── /panel ────────────────────────────────────────────
    @app_commands.command(name="panel", description="Security Control Panel")
    async def panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        cfg      = await Database.get_all_config(gid)
        disabled = await Database.get_disabled_modules(gid)
        await edit_original_cv2(interaction, build_panel(cfg, disabled, self._icon(interaction.guild)))

    # ── /status ───────────────────────────────────────────
    @app_commands.command(name="status", description="Security status overview")
    async def status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        cfg      = await Database.get_all_config(gid)
        disabled = await Database.get_disabled_modules(gid)
        wl_u     = len(await Database.get_whitelist_users(gid))
        wl_b     = len(await Database.get_whitelist_bots(gid))
        wl_c     = len(await Database.get_whitelist_channels(gid))
        await edit_original_cv2(
            interaction,
            build_status(cfg, disabled, wl_u, wl_b, wl_c, self._icon(interaction.guild)),
            ephemeral=True
        )

    # ── /logs ─────────────────────────────────────────────
    @app_commands.command(name="logs", description="Recent security events")
    @app_commands.describe(amount="Number of events (1-20, default 10)")
    async def logs(self, interaction: discord.Interaction, amount: int = 10):
        await interaction.response.defer(ephemeral=True)
        gid = str(interaction.guild_id)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        amount = min(max(amount, 1), 20)
        events = await Database.get_recent_events(gid, amount)
        await edit_original_cv2(
            interaction,
            build_logs(events, self._icon(interaction.guild)),
            ephemeral=True
        )

    # ── /sechelp ──────────────────────────────────────────
    @app_commands.command(name="sechelp", description="All security bot commands")
    async def sechelp(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        gid      = str(interaction.guild_id)
        is_owner = await self._is_owner(interaction.guild_id, interaction.user.id)
        is_wl    = await Database.is_whitelist_user(gid, str(interaction.user.id))
        if not is_owner and not is_wl:
            await no_access(interaction); return
        await edit_original_cv2(interaction, build_help(is_owner), ephemeral=True)

    # ── Button interactions ───────────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        if not interaction.guild_id:
            return

        cid      = interaction.data.get("custom_id", "")
        gid      = str(interaction.guild_id)
        is_owner = await self._is_owner(interaction.guild_id, interaction.user.id)
        is_admin = interaction.user.guild_permissions.administrator

        # ── Bot Accept / Deny ──────────────────────────────
        if cid.startswith("bot_accept_") or cid.startswith("bot_deny_"):
            if not is_owner and not is_admin:
                await respond_cv2(interaction, [
                    {"type": 17, "accent_color": 0xED4245, "components": [
                        {"type": 10, "content": "> Access Denied\nAdministrators only."}
                    ]}
                ], ephemeral=True)
                return

            bot_id     = cid.replace("bot_accept_", "").replace("bot_deny_", "")
            bot_member = interaction.guild.get_member(int(bot_id))
            name       = bot_member.name if bot_member else bot_id
            avatar     = str(bot_member.display_avatar.url) if bot_member else None

            if cid.startswith("bot_accept_"):
                if bot_member:
                    tasks = [ch.set_permissions(bot_member, overwrite=None, reason="Bot accepted")
                             for ch in interaction.guild.channels]
                    await asyncio.gather(*tasks, return_exceptions=True)
                section = {
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> Bot Accepted\n"
                        f"• Bot: **{name}**\n"
                        f"• Accepted by: {interaction.user.mention}"
                    )}]
                }
                if avatar:
                    section["accessory"] = {"type": 11, "media": {"url": avatar}}
                await update_cv2(interaction, [{"type": 17, "accent_color": 0x57F287, "components": [section]}])
            else:
                kicked = False
                if bot_member:
                    try:
                        await bot_member.kick(reason=f"Bot denied by {interaction.user}")
                        kicked = True
                    except Exception:
                        pass
                section = {
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> Bot Denied\n"
                        f"• Bot: **{name}**\n"
                        f"• Denied by: {interaction.user.mention}\n"
                        f"• Kicked: {'Yes' if kicked else 'No'}"
                    )}]
                }
                if avatar:
                    section["accessory"] = {"type": 11, "media": {"url": avatar}}
                await update_cv2(interaction, [{"type": 17, "accent_color": 0xED4245, "components": [section]}])
            return

        # ── Panel buttons — owner only ─────────────────────
        if not is_owner:
            await respond_cv2(interaction, [
                {"type": 17, "accent_color": 0xED4245, "components": [
                    {"type": 10, "content": "> Access Denied\nServer Owners only."}
                ]}
            ], ephemeral=True)
            return

        TOGGLE_MAP = {
            "sec_toggle_alt":       "alt",
            "sec_toggle_link":      "link",
            "sec_toggle_token":     "token",
            "sec_toggle_spam":      "spam",
            "sec_toggle_mass":      "mass_action",
            "sec_toggle_role":      "role_action",
            "sec_toggle_botverify": "bot_verify",
        }

        if cid in TOGGLE_MAP:
            module = TOGGLE_MAP[cid]
            if await Database.is_module_enabled(gid, module):
                await Database.disable_module(gid, module)
            else:
                await Database.enable_module(gid, module)
            cfg      = await Database.get_all_config(gid)
            disabled = await Database.get_disabled_modules(gid)
            await update_cv2(interaction, build_panel(cfg, disabled, self._icon(interaction.guild)))
            return

        if cid == "sec_toggle_altkick":
            current = await Database.get_config(gid, "alt_action", "kick")
            await Database.set_config(gid, "alt_action", "log" if current == "kick" else "kick")
            cfg      = await Database.get_all_config(gid)
            disabled = await Database.get_disabled_modules(gid)
            await update_cv2(interaction, build_panel(cfg, disabled, self._icon(interaction.guild)))
            return

        if cid == "sec_status":
            cfg      = await Database.get_all_config(gid)
            disabled = await Database.get_disabled_modules(gid)
            wl_u     = len(await Database.get_whitelist_users(gid))
            wl_b     = len(await Database.get_whitelist_bots(gid))
            wl_c     = len(await Database.get_whitelist_channels(gid))
            await respond_cv2(interaction,
                build_status(cfg, disabled, wl_u, wl_b, wl_c, self._icon(interaction.guild)),
                ephemeral=True)
            return

        if cid == "sec_logs_btn":
            events = await Database.get_recent_events(gid, 10)
            await respond_cv2(interaction, build_logs(events, self._icon(interaction.guild)), ephemeral=True)
            return


async def setup(bot: commands.Bot):
    await bot.add_cog(Panel(bot))

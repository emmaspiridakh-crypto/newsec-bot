import asyncio
import datetime
import re

import discord
from discord.ext import commands

from database import Database
from utils.cv2_helper import send_cv2
from utils.tracker import spam_tracker

URL_PATTERN   = re.compile(r"(https?://|www\.)\S+|discord\.gg/\S+", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"[MNO][a-zA-Z0-9_-]{23,25}\.[a-zA-Z0-9_-]{6}\.[a-zA-Z0-9_-]{27,38}")


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _log_id(self, guild_id: str) -> int | None:
        val = await Database.get_setting(guild_id, "log_channel_id")
        return int(val) if val else None

    async def _send_log(self, guild_id: str, components: list):
        cid = await self._log_id(guild_id)
        if cid:
            await send_cv2(cid, components)

    # ── on_member_join ────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        gid   = str(guild.id)

        # ── Bot Verification ──────────────────────────────
        if member.bot:
            if not await Database.is_module_enabled(gid, "bot_verify"):
                return
            if await Database.is_whitelist_bot(gid, str(member.id)):
                return

            tasks = [
                ch.set_permissions(
                    member,
                    view_channel=False, send_messages=False,
                    connect=False, speak=False, add_reactions=False,
                    reason="Bot pending verification"
                )
                for ch in guild.channels
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            is_verified = getattr(member.public_flags, "verified_bot", False)
            accent      = 0x5865F2 if is_verified else 0x8B0000
            status_line = "Verified bot." if is_verified else "Unverified — manual approval required."

            cid = await self._log_id(gid)
            if cid:
                await send_cv2(cid, [{
                    "type": 17,
                    "accent_color": accent,
                    "components": [
                        {
                            "type": 9,
                            "components": [{"type": 10, "content": (
                                f"> New Bot\n"
                                f"• Name: **{member.name}**  ({member.id})\n"
                                f"• Status: {status_line}\n"
                                f"• Created: <t:{int(member.created_at.timestamp())}:F>\n"
                                f"• Permissions: Zero — pending approval"
                            )}],
                            "accessory": {"type": 11, "media": {"url": str(member.display_avatar.url)}}
                        },
                        {"type": 14},
                        {
                            "type": 1,
                            "components": [
                                {"type": 2, "label": "Accept",      "style": 3, "custom_id": f"bot_accept_{member.id}"},
                                {"type": 2, "label": "Deny — Kick", "style": 4, "custom_id": f"bot_deny_{member.id}"}
                            ]
                        }
                    ]
                }])

            await Database.log_event(gid, "bot_join", {
                "bot":      member.name,
                "id":       str(member.id),
                "verified": is_verified
            })
            return

        # ── ALT Detection ─────────────────────────────────
        if not await Database.is_module_enabled(gid, "alt"):
            return

        age_days  = (discord.utils.utcnow() - member.created_at).days
        threshold = int(await Database.get_config(gid, "alt_age_days", "30"))
        if age_days >= threshold:
            return

        alt_action   = await Database.get_config(gid, "alt_action", "kick")
        action_label = "Log only"
        action_ok    = True

        if alt_action == "kick":
            try:
                await member.kick(reason=f"Alt account — age: {age_days} days")
                action_label = "Kicked"
            except Exception as e:
                action_label = f"Kick failed: {e}"
                action_ok    = False

        await Database.log_event(gid, "alt_detected", {
            "user": str(member), "id": str(member.id),
            "age": age_days, "action": alt_action
        })

        await self._send_log(gid, [{
            "type": 17,
            "accent_color": 0x8B0000,
            "components": [
                {
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> ALT ACCOUNT DETECTED\n"
                        f"• User: {member.mention}  ({member.id})\n"
                        f"• Account age: **{age_days} days**  (threshold: {threshold} days)\n"
                        f"• Created: <t:{int(member.created_at.timestamp())}:F>"
                    )}],
                    "accessory": {"type": 11, "media": {"url": str(member.display_avatar.url)}}
                },
                {"type": 14},
                {"type": 10, "content": f"> Action: {'Completed' if action_ok else 'Failed'}  —  {action_label}"}
            ]
        }])

    # ── on_message ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot:
            return

        author = message.author
        member = author if isinstance(author, discord.Member) else message.guild.get_member(author.id)
        if not member:
            return

        gid        = str(message.guild.id)
        is_owner   = await Database.is_server_owner(gid, str(author.id), self.bot.installer_id)
        is_admin   = member.guild_permissions.administrator
        is_wl_user = await Database.is_whitelist_user(gid, str(author.id))
        is_wl_ch   = await Database.is_whitelist_channel(gid, str(message.channel.id))
        is_wl_role = await Database.has_whitelist_role(gid, [r.id for r in member.roles])

        # ── Token Detection ───────────────────────────────
        if await Database.is_module_enabled(gid, "token") and TOKEN_PATTERN.search(message.content):
            try:
                await message.delete()
            except Exception:
                pass

            await Database.log_event(gid, "token_detected", {
                "user": str(author), "id": str(author.id), "channel": message.channel.name
            })

            await self._send_log(gid, [{
                "type": 17,
                "accent_color": 0x8B0000,
                "components": [{
                    "type": 9,
                    "components": [{"type": 10, "content": (
                        f"> TOKEN DETECTED\n"
                        f"• User: {author.mention}  ({author.id})\n"
                        f"• Channel: {message.channel.mention}\n"
                        f"• Message deleted.\n\n"
                        f"> If this is your token, reset it immediately."
                    )}],
                    "accessory": {"type": 11, "media": {"url": str(author.display_avatar.url)}}
                }]
            }])
            return

        # ── Link Detection ────────────────────────────────
        if await Database.is_module_enabled(gid, "link") and URL_PATTERN.search(message.content):
            allowed = is_owner or is_admin or is_wl_user or is_wl_role or is_wl_ch
            if not allowed:
                try:
                    await message.delete()
                except Exception:
                    pass

                link_to = int(await Database.get_config(gid, "link_timeout_mins", "60"))
                try:
                    await member.timeout(datetime.timedelta(minutes=link_to), reason="Link detected")
                except Exception:
                    pass

                await Database.log_event(gid, "link_detected", {
                    "user": str(author), "id": str(author.id), "channel": message.channel.name
                })

                await self._send_log(gid, [{
                    "type": 17,
                    "accent_color": 0xE67E22,
                    "components": [{
                        "type": 9,
                        "components": [{"type": 10, "content": (
                            f"> LINK DETECTED\n"
                            f"• User: {author.mention}  ({author.id})\n"
                            f"• Channel: {message.channel.mention}\n"
                            f"• Timeout: {link_to} min\n"
                            f"• Message deleted."
                        )}],
                        "accessory": {"type": 11, "media": {"url": str(author.display_avatar.url)}}
                    }]
                }])
                return

        # ── Spam Detection ────────────────────────────────
        if await Database.is_module_enabled(gid, "spam") and not is_owner and not is_admin:
            thr  = int(await Database.get_config(gid, "spam_threshold",    "5"))
            win  = int(await Database.get_config(gid, "spam_window_secs",  "5"))
            tout = int(await Database.get_config(gid, "spam_timeout_mins", "10"))
            key  = f"spam_{message.guild.id}_{author.id}"

            if spam_tracker.add_and_check(key, thr, win):
                spam_tracker.reset(key)
                try:
                    await member.timeout(datetime.timedelta(minutes=tout), reason="Spam")
                except Exception:
                    pass

                await Database.log_event(gid, "spam_detected", {
                    "user": str(author), "id": str(author.id), "channel": message.channel.name
                })

                await self._send_log(gid, [{
                    "type": 17,
                    "accent_color": 0xED4245,
                    "components": [{
                        "type": 9,
                        "components": [{"type": 10, "content": (
                            f"> SPAM DETECTED\n"
                            f"• User: {author.mention}  ({author.id})\n"
                            f"• Channel: {message.channel.mention}\n"
                            f"• Timeout: {tout} min\n"
                            f"• Threshold: {thr} msgs / {win}s"
                        )}],
                        "accessory": {"type": 11, "media": {"url": str(author.display_avatar.url)}}
                    }]
                }])


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))

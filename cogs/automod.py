import asyncio
import datetime
import re

import discord
from discord.ext import commands

from database import Database
from utils import logger, quarantine, ui
from utils.audit import find_entry
from utils.tracker import spam_tracker

URL_PATTERN   = re.compile(r"(https?://|www\.)\S+|discord\.gg/\S+", re.IGNORECASE)
TOKEN_PATTERN = re.compile(r"[MNO][a-zA-Z0-9_-]{23,25}\.[a-zA-Z0-9_-]{6}\.[a-zA-Z0-9_-]{27,38}")
BOT_NAME_PATTERN = re.compile(r"^[a-z]+[._-]?\d{3,}$", re.IGNORECASE)


def alt_score(member: discord.Member, age_days: int, threshold_days: int) -> tuple:
    """Σκορ υποψίας για alt account. Επιστρέφει (πόντοι, [λόγοι])."""
    score, reasons = 0, []
    if age_days < threshold_days:
        score += 50
        reasons.append(f"account age {age_days}d < {threshold_days}d (+50)")
        if age_days < 7:
            score += 20
            reasons.append("account younger than 7 days (+20)")
    elif age_days < threshold_days * 2:
        score += 25
        reasons.append(f"account age {age_days}d < {threshold_days * 2}d (+25)")
    if member.avatar is None:
        score += 20
        reasons.append("default avatar (+20)")
    if BOT_NAME_PATTERN.match(member.name or ""):
        score += 15
        reasons.append("auto-generated looking username (+15)")
    if not member.public_flags.value:
        score += 10
        reasons.append("no profile badges (+10)")
    return score, reasons


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── on_member_join ────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        gid   = str(guild.id)

        # ── Bot Verification ──────────────────────────────
        if member.bot:
            if not await Database.is_module_enabled(gid, "bot_verify"):
                return
            if await Database.is_whitelist_bot(gid, str(member.id)) or member.id == self.bot.user.id:
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
            entry       = await find_entry(guild, discord.AuditLogAction.bot_add,
                                           target_id=member.id, max_age=15, delay=0.3)
            adder       = f"{entry.user.mention}" if entry and entry.user else "unknown"

            await logger.log(gid, "bot", "New bot — pending verification", [
                f"• Name: **{member.name}**  ({member.id})",
                f"• Added by: {adder}",
                f"• Status: {status_line}",
                f"• Created: <t:{int(member.created_at.timestamp())}:F>",
                "• Permissions: Zero — pending approval",
                "• Accepting it also whitelists it for Anti-Nuke and AutoMod.",
            ], thumb=str(member.display_avatar.url), color=accent, urgent=True,
                extra=[ui.row(ui.btn("Accept", f"bot_accept_{member.id}", 3),
                               ui.btn("Deny — Kick", f"bot_deny_{member.id}", 4))],
                event=("bot_join", {"bot": member.name, "id": str(member.id), "verified": is_verified}))
            return

        # ── ALT Detection ─────────────────────────────────
        if not await Database.is_module_enabled(gid, "alt"):
            return

        age_days  = (discord.utils.utcnow() - member.created_at).days
        threshold = await Database.get_int(gid, "alt_age_days", 30)
        need      = await Database.get_int(gid, "alt_score_threshold", 50)
        score, reasons = alt_score(member, age_days, threshold)
        if score < need:
            return

        alt_action   = await Database.get_config(gid, "alt_action", "kick")
        action_label = "Log only"
        action_ok    = True

        if alt_action == "kick":
            try:
                await member.kick(reason=f"Alt account — score {score}, age {age_days}d")
                action_label = "Kicked"
            except Exception as e:
                action_label, action_ok = f"Kick failed: {e}", False
        elif alt_action == "quarantine":
            q = await quarantine.apply(guild, member, f"Alt account (score {score})", strip=False)
            action_label = "Quarantined" if q["ok"] else "Quarantine failed"
            action_ok    = q["ok"]

        await logger.log(gid, "security", "ALT account detected", [
            f"• User: {member.mention}  ({member.id})",
            f"• Score: **{score}** (threshold {need})",
            "• Reasons: " + "; ".join(reasons),
            f"• Created: <t:{int(member.created_at.timestamp())}:F>",
            f"• Action: {'Completed' if action_ok else 'Failed'} — {action_label}",
        ], thumb=str(member.display_avatar.url), color=0xE67E22,
            event=("alt_detected", {"user": str(member), "id": str(member.id),
                                    "age": age_days, "score": score, "action": alt_action}))

    # ── Messages ──────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self._scan(message, edited=False)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        # Αθώο μήνυμα που αλλάζει αργότερα σε link/token πρέπει να πιάνεται
        if before.content == after.content:
            return
        await self._scan(after, edited=True)

    async def _scan(self, message: discord.Message, edited: bool):
        if not message.guild or message.author.id == self.bot.user.id:
            return
        if not message.content:
            return

        gid    = str(message.guild.id)
        author = message.author

        # ── Bots & webhooks ───────────────────────────────
        if author.bot or message.webhook_id:
            await self._scan_bot_message(message, gid, edited)
            return

        member = author if isinstance(author, discord.Member) else message.guild.get_member(author.id)
        if not member:
            return

        is_owner   = await Database.is_server_owner(gid, str(author.id), self.bot.installer_id)
        is_admin   = member.guild_permissions.administrator
        is_wl_user = await Database.is_whitelist_user(gid, str(author.id))
        is_wl_ch   = await Database.is_whitelist_channel(gid, str(message.channel.id))
        is_wl_role = await Database.has_whitelist_role(gid, [r.id for r in member.roles])
        suffix     = " (edited message)" if edited else ""

        # ── Token Detection ───────────────────────────────
        if await Database.is_module_enabled(gid, "token") and TOKEN_PATTERN.search(message.content):
            await self._delete(message)
            await logger.log(gid, "security", "Token detected" + suffix, [
                f"• User: {author.mention}  ({author.id})",
                f"• Channel: {message.channel.mention}",
                "• Message deleted.", "", "> If this is your token, reset it immediately.",
            ], thumb=str(author.display_avatar.url), color=0x8B0000,
                event=("token_detected", {"user": str(author), "id": str(author.id),
                                          "channel": message.channel.name}))
            return

        # ── Link Detection ────────────────────────────────
        if await Database.is_module_enabled(gid, "link") and URL_PATTERN.search(message.content):
            if not (is_owner or is_admin or is_wl_user or is_wl_role or is_wl_ch):
                await self._delete(message)
                link_to = await Database.get_int(gid, "link_timeout_mins", 60)
                try:
                    await member.timeout(datetime.timedelta(minutes=link_to), reason="Link detected")
                except Exception:
                    pass
                await logger.log(gid, "security", "Link detected" + suffix, [
                    f"• User: {author.mention}  ({author.id})",
                    f"• Channel: {message.channel.mention}",
                    f"• Timeout: {link_to} min", "• Message deleted.",
                ], thumb=str(author.display_avatar.url), color=0xE67E22,
                    event=("link_detected", {"user": str(author), "id": str(author.id),
                                             "channel": message.channel.name}))
                return

        # ── Spam Detection (όχι σε edits) ─────────────────
        if edited:
            return
        if await Database.is_module_enabled(gid, "spam") and not is_owner and not is_admin:
            thr  = await Database.get_int(gid, "spam_threshold", 5)
            win  = await Database.get_int(gid, "spam_window_secs", 5)
            tout = await Database.get_int(gid, "spam_timeout_mins", 10)
            key  = f"spam_{message.guild.id}_{author.id}"
            if spam_tracker.add_and_check(key, thr, win):
                spam_tracker.reset(key)
                try:
                    await member.timeout(datetime.timedelta(minutes=tout), reason="Spam")
                except Exception:
                    pass
                await logger.log(gid, "security", "Spam detected", [
                    f"• User: {author.mention}  ({author.id})",
                    f"• Channel: {message.channel.mention}",
                    f"• Timeout: {tout} min", f"• Threshold: {thr} msgs / {win}s",
                ], thumb=str(author.display_avatar.url), color=0xED4245,
                    event=("spam_detected", {"user": str(author), "id": str(author.id),
                                             "channel": message.channel.name}))

    async def _scan_bot_message(self, message: discord.Message, gid: str, edited: bool):
        """Bots/webhooks περνούν από τα ίδια φίλτρα, εκτός αν είναι whitelisted."""
        if not await Database.is_module_enabled(gid, "automod_bots"):
            return
        author = message.author
        if await Database.is_whitelist_bot(gid, str(author.id)):
            return
        if await Database.is_whitelist_channel(gid, str(message.channel.id)):
            return

        reason = None
        if await Database.is_module_enabled(gid, "token") and TOKEN_PATTERN.search(message.content):
            reason = "Token"
        elif await Database.is_module_enabled(gid, "link") and URL_PATTERN.search(message.content):
            reason = "Link"
        if not reason:
            return

        await self._delete(message)
        kind = "webhook" if message.webhook_id else "bot"
        await logger.log(gid, "security", f"{reason} from {kind} blocked" + (" (edited)" if edited else ""), [
            f"• Source: **{author.name}**  ({author.id})",
            f"• Channel: {message.channel.mention}",
            "• Message deleted.",
            "• If this is a trusted bot, use `/whitelist bot` (or whitelist the channel).",
        ], thumb=str(author.display_avatar.url), color=0xE67E22,
            event=("bot_message_blocked", {"user": str(author), "id": str(author.id), "kind": kind,
                                           "reason": reason}))

    @staticmethod
    async def _delete(message: discord.Message):
        try:
            await message.delete()
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))

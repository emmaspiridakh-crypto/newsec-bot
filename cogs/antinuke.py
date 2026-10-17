import asyncio
import datetime
import time

import discord
from discord.ext import commands
from discord import app_commands

from database import Database
from utils import lockdown, logger, quarantine, ui
from utils.audit import find_entry
from utils.cv2_helper import respond_cv2, edit_original_cv2, edit_cv2, no_access
from utils.security import DANGEROUS_PERMS, is_dangerous, below_bot, dangerous_member_roles
from utils.tracker import (
    ban_tracker, kick_tracker, channel_del_tracker, role_action_tracker, webhook_tracker,
    channel_create_tracker, channel_edit_tracker, emoji_tracker, invite_tracker,
    purge_tracker, everyone_tracker, leave_tracker,
)

AA = discord.AuditLogAction


class AntiNuke(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self._recent = {}       # (guild_id, user_id) -> timestamp του τελευταίου πλήρους trigger
        self._seen   = set()    # audit entry ids που έχουν ήδη χειριστεί (prune)

    # ── Helpers ────────────────────────────────────────────
    async def _is_owner(self, guild_id: int, uid: int) -> bool:
        return await Database.is_server_owner(str(guild_id), str(uid), self.bot.installer_id)

    async def _exempt(self, guild: discord.Guild, user) -> bool:
        """Owners, trusted admins, whitelisted bots και το ίδιο το bot δεν τιμωρούνται.
        Τα υπόλοιπα bots ΔΕΝ εξαιρούνται πια."""
        if user is None or user.id == self.bot.user.id:
            return True
        gid = str(guild.id)
        if await Database.is_server_owner(gid, str(user.id), self.bot.installer_id):
            return True
        if await Database.is_trusted(gid, str(user.id)):
            return True
        if user.bot and await Database.is_whitelist_bot(gid, str(user.id)):
            return True
        return False

    async def _member(self, guild: discord.Guild, uid: int) -> discord.Member | None:
        m = guild.get_member(uid)
        if m:
            return m
        try:
            return await guild.fetch_member(uid)
        except Exception:
            return None

    # ── Punishment ─────────────────────────────────────────
    async def _punish(self, guild: discord.Guild, member: discord.Member, reason: str) -> dict:
        """1) αφαίρεση επικίνδυνων roles  2) timeout  3) αν αποτύχει → Quarantine role.
        Για bots: strip + kick."""
        gid   = str(guild.id)
        hours = max(1, min(672, await Database.get_int(gid, "nuke_timeout_hours", 168)))
        res = {"is_bot": member.bot, "stripped": [], "timeout": False, "timeout_hours": hours,
               "quarantined": False, "kicked": False, "warning": None}

        if member.id == guild.owner_id:
            res["warning"] = "target is the server owner — cannot be punished"
            return res

        removable = dangerous_member_roles(guild, member)
        if removable:
            try:
                await member.remove_roles(*removable, reason=f"Anti-Nuke: {reason}"[:400])
                res["stripped"] = [r.name for r in removable]
                if not member.bot:
                    await Database.save_quarantine(gid, str(member.id), [r.id for r in removable], reason)
            except Exception as e:
                print(f"[AntiNuke] role strip failed: {e}")

        if member.bot:
            try:
                await member.kick(reason=f"Anti-Nuke: {reason}"[:400])
                res["kicked"] = True
            except Exception as e:
                print(f"[AntiNuke] bot kick failed: {e}")
                res["warning"] = "bot could not be kicked it outranks this bot in the roles"
            return res

        try:
            await member.timeout(datetime.timedelta(hours=hours), reason=f"Anti-Nuke: {reason}"[:400])
            res["timeout"] = True
        except Exception as e:
            print(f"[AntiNuke] timeout failed: {e}")
            q = await quarantine.apply(guild, member, reason)
            res["quarantined"] = q["ok"]
            res["stripped"] += [r.name for r in q["stripped"] if r.name not in res["stripped"]]

        if not res["timeout"] and not res["quarantined"]:
            res["warning"] = ("timeout and quarantine failed this member likely outranks the bot"
                              "in the role hierarchy. Move the bot's role to the top.")
        return res

    # ── Trigger: punish → (lockdown | log | DM) παράλληλα → heal ──
    async def trigger(self, guild: discord.Guild, user, reason: str, *, heal: bool = False,
                      event: str = "mass_action"):
        gid, now = str(guild.id), time.time()
        lockdown.pause_backups(self.bot, gid)

        member = await self._member(guild, user.id)
        key    = (gid, user.id)
        last   = self._recent.get(key)
        if last and now - last < 45:
            if member:
                await self._punish(guild, member, reason)    # σιωπηρά — έχει ήδη γίνει alert
            return
        self._recent[key] = now
        if len(self._recent) > 500:
            self._recent = {k: v for k, v in self._recent.items() if now - v < 120}

        # 1) τιμωρία ΠΡΩΤΑ, ώστε να σταματήσει ο δράστης
        if member:
            punish = await self._punish(guild, member, reason)
        else:
            punish = {"is_bot": getattr(user, "bot", False), "stripped": [], "timeout": False,
                      "timeout_hours": 0, "quarantined": False, "kicked": False,
                      "warning": "actor is no longer in the server"}

        await Database.log_event(gid, event, {
            "user": str(user), "id": str(user.id), "reason": reason,
        })

        # 2) lockdown + log + DM ΜΑΖΙ
        dm_line_holder = {"v": "DM: sending…"}

        def comps(lock, dm):
            return lockdown.alert_components(guild.id, title="ANTI-NUKE TRIGGERED", actor=user,
                                             reason=reason, punish=punish, lock=lock, dm_line=dm)

        async def do_lock():
            return await lockdown.engage(self.bot, guild, kind="antinuke", reason=reason, actor_id=user.id)

        async def do_log():
            return await self._send_alert(gid, comps(None, dm_line_holder["v"]))

        async def do_dm():
            line = await lockdown.notify_owners(self.bot, guild, f"Anti-Nuke triggered in {guild.name}", [
                f"**Actor:** {user} ({user.id})" + (" [BOT]" if getattr(user, "bot", False) else ""),
                f"**Trigger:** {reason}",
                f"**Roles stripped:** {', '.join(punish['stripped']) or 'none'}",
                "**Action:** " + ("timeout applied" if punish["timeout"] else
                                  "quarantine role applied" if punish["quarantined"] else
                                  "bot kicked" if punish["kicked"] else "PUNISHMENT FAILED"),
                "**Lockdown:** use the Restore button in the log channel or `/panel` to lift it.",
            ])
            dm_line_holder["v"] = line
            return line

        lock, msg, dm_line = await asyncio.gather(do_lock(), do_log(), do_dm(), return_exceptions=True)
        if isinstance(lock, Exception):
            print(f"[AntiNuke] lockdown failed: {lock}")
            lock = {"channels_locked": 0, "channels_total": 0, "roles_stripped": 0, "roles_total": 0}
        if isinstance(dm_line, Exception):
            print(f"[AntiNuke] DM step failed: {dm_line!r}")
            dm_line = "DM failed"
        if isinstance(msg, Exception):
            print(f"[AntiNuke] alert failed: {msg!r}")

        # 3) ενημέρωση του log message με το τελικό αποτέλεσμα
        if isinstance(msg, dict) and msg.get("id"):
            await edit_cv2(int(msg["channel_id"]), int(msg["id"]), comps(lock, dm_line))

        # 4) auto-restore (δομή server) αν διαγράφηκαν κανάλια/roles
        if heal and await Database.is_module_enabled(gid, "auto_restore"):
            backup = self.bot.get_cog("Backup")
            if backup:
                try:
                    healed = await backup.restore_missing(guild, before_ts=int(now) - 60)
                    if healed and (healed["channels"] or healed["roles"]):
                        await lockdown.lock_new(self.bot, guild, healed["new_channels"],
                                                healed["new_roles"], reason)
                        await logger.log(gid, "security", "Auto-restore", [
                            f"• Recreated {len(healed['roles'])} role(s) and {len(healed['channels'])} channel(s) "
                            f"from backup **#{healed['backup_id']}**",
                            "• New channels/roles were locked as part of the lockdown.",
                        ], event=("auto_restore", {"user": "system", "roles": len(healed["roles"]),
                                                   "channels": len(healed["channels"])}))
                except Exception as e:
                    print(f"[AntiNuke] auto-restore failed: {e}")

    async def _send_alert(self, gid: str, components: list):
        """Το alert πάει πάντα στο log channel, ανεξάρτητα από το αν το Security log είναι ενεργό
        (είναι κρίσιμο: περιέχει το Restore)."""
        cid = await Database.get_setting(gid, "log_channel_id")
        if not cid:
            return None
        from utils.cv2_helper import send_cv2
        return await send_cv2(int(cid), components)

    #  ── Γενικός έλεγχος "μαζικής" ενέργειας ────────────────
    async def _check(self, guild, *, module, action, tracker, name, limit_key, window_key,
                     label, target_id=None, heal=False, entry=None, count=1):
        gid = str(guild.id)
        if not await Database.is_module_enabled(gid, module):
            return
        if entry is None:
            entry = await find_entry(guild, action, target_id=target_id)
        if entry is None or entry.user is None:
            return
        user = entry.user
        if await self._exempt(guild, user):
            return
        limit  = await Database.get_int(gid, limit_key, 3)
        window = await Database.get_int(gid, window_key, 10)
        key    = f"{name}_{guild.id}_{user.id}"
        hit = False
        for _ in range(count):
            if tracker.add_and_check(key, limit, window):
                hit = True
        if hit:
            tracker.reset(key)
            await self.trigger(guild, user, f"{label} ({limit}/{window}s)", heal=heal)

    # ── Lockdown: Restore button ───────────────────────────
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if not cid.startswith("lk_restore_") or not cid.endswith("_a"):
            return                       # τα "_p" (panel) τα χειρίζεται το panel cog
        gid = cid[len("lk_restore_"):-2]
        if not interaction.guild_id or str(interaction.guild_id) != gid:
            await respond_cv2(interaction, [ui.container([ui.text("> Error\nWrong server.")], 0xED4245)],
                              ephemeral=True)
            return
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await respond_cv2(interaction, [ui.container(
                [ui.text("> Access Denied\nServer Owners only.")], 0xED4245)], ephemeral=True)
            return
        await interaction.response.defer()
        stats = await lockdown.restore(self.bot, interaction.guild, actor=interaction.user)
        if stats is None:
            comps = [ui.container([ui.text("> No active lockdown\nNothing to restore.")], 0x5865F2)]
        else:
            comps = lockdown.restored_components(interaction.user, stats)
            await logger.log(gid, "security", "Lockdown lifted", [
                f"• By: {interaction.user.mention}",
                f"• Channels restored: {stats['channels_locked']}/{stats['channels_total']}",
                f"• Roles restored: {stats['roles_stripped']}/{stats['roles_total']}",
            ])
        await edit_original_cv2(interaction, comps)

    @app_commands.command(name="unlock", description="Lift an active lockdown (restores channels and role permissions)")
    async def unlock(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await self._is_owner(interaction.guild_id, interaction.user.id):
            await no_access(interaction); return
        stats = await lockdown.restore(self.bot, interaction.guild, actor=interaction.user)
        if stats is None:
            await edit_original_cv2(interaction, [ui.container(
                [ui.text("> No active lockdown\nNothing to unlock.")], 0x5865F2)], ephemeral=True)
            return
        await logger.log(str(interaction.guild_id), "security", "Lockdown lifted", [
            f"• By: {interaction.user.mention}",
            f"• Channels restored: {stats['channels_locked']}/{stats['channels_total']}",
            f"• Roles restored: {stats['roles_stripped']}/{stats['roles_total']}",
        ])
        await edit_original_cv2(interaction, lockdown.restored_components(interaction.user, stats),
                                ephemeral=True)

    # ── Mass Ban ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        await self._check(guild, module="mass_action", action=AA.ban, tracker=ban_tracker,
                          name="ban", limit_key="mass_action_limit", window_key="mass_action_window",
                          label="Mass Ban", target_id=user.id)

    # ── Mass Kick + Prune ──────────────────────────────────
    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        entry = await find_entry(guild, AA.kick, target_id=member.id, max_age=6)
        if entry is not None:
            await self._check(guild, module="mass_action", action=AA.kick, tracker=kick_tracker,
                              name="kick", limit_key="mass_action_limit",
                              window_key="mass_action_window", label="Mass Kick", entry=entry)
            return
        # Member prune: πολλές αποχωρήσεις μαζί → ψάχνουμε member_prune entry
        if leave_tracker.add_and_check(f"leave_{gid}", 5, 5):
            leave_tracker.reset(f"leave_{gid}")
            pr = await find_entry(guild, AA.member_prune, max_age=20)
            if pr is not None and pr.id not in self._seen and pr.user is not None:
                self._seen.add(pr.id)
                if len(self._seen) > 200:
                    self._seen.clear()
                if not await self._exempt(guild, pr.user):
                    await self.trigger(guild, pr.user, "Member Prune")

    # ── Channels ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        await self._check(channel.guild, module="mass_action", action=AA.channel_delete,
                          tracker=channel_del_tracker, name="ch_del", limit_key="mass_action_limit",
                          window_key="mass_action_window", label="Mass Channel Delete",
                          target_id=channel.id, heal=True)

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel):
        await quarantine.protect_channel(channel.guild, channel)
        await self._check(channel.guild, module="channel_guard", action=AA.channel_create,
                          tracker=channel_create_tracker, name="ch_new", limit_key="chan_action_limit",
                          window_key="chan_action_window", label="Mass Channel Create",
                          target_id=channel.id)

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        # Μόνο αλλαγές ονόματος/overwrites μετράνε — όχι reorder (position/category)
        if before.name == after.name and before.overwrites == after.overwrites:
            return
        guild = after.guild
        entry = await find_entry(guild, AA.channel_update, target_id=after.id)
        if entry is None:
            entry = await find_entry(guild, AA.overwrite_update, target_id=after.id, delay=0)
        await self._check(guild, module="channel_guard", action=AA.channel_update,
                          tracker=channel_edit_tracker, name="ch_edit", limit_key="chan_action_limit",
                          window_key="chan_action_window", label="Mass Channel Edit", entry=entry)

    # ── Webhooks ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_webhook_update(self, channel: discord.abc.GuildChannel):
        await self._check(channel.guild, module="mass_action", action=AA.webhook_create,
                          tracker=webhook_tracker, name="webhook", limit_key="mass_action_limit",
                          window_key="mass_action_window", label="Mass Webhook Create")

    # ── Emoji / Stickers ───────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        gone = {e.id for e in before} - {e.id for e in after}
        if gone:
            await self._check(guild, module="emoji_guard", action=AA.emoji_delete,
                              tracker=emoji_tracker, name="emoji", limit_key="emoji_action_limit",
                              window_key="emoji_action_window", label="Mass Emoji Delete",
                              count=len(gone))

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after):
        gone = {s.id for s in before} - {s.id for s in after}
        if gone:
            await self._check(guild, module="emoji_guard", action=AA.sticker_delete,
                              tracker=emoji_tracker, name="sticker", limit_key="emoji_action_limit",
                              window_key="emoji_action_window", label="Mass Sticker Delete",
                              count=len(gone))

    # ── Bulk message delete (purge) ────────────────────────
    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        if not payload.guild_id or len(payload.message_ids) < 10:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        await self._check(guild, module="purge_guard", action=AA.message_bulk_delete,
                          tracker=purge_tracker, name="purge", limit_key="purge_limit",
                          window_key="purge_window", label="Mass Message Purge",
                          target_id=payload.channel_id)

    # ── Invite spam ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        guild = invite.guild
        if not isinstance(guild, discord.Guild):
            guild = self.bot.get_guild(getattr(guild, "id", 0))
        if guild is None:
            return
        gid = str(guild.id)
        if not await Database.is_module_enabled(gid, "invite_guard"):
            return
        user = invite.inviter
        if user is None:
            entry = await find_entry(guild, AA.invite_create)
            user  = entry.user if entry else None
        if user is None or await self._exempt(guild, user):
            return
        limit  = await Database.get_int(gid, "invite_action_limit", 5)
        window = await Database.get_int(gid, "invite_action_window", 10)
        key    = f"invite_{guild.id}_{user.id}"
        if invite_tracker.add_and_check(key, limit, window):
            invite_tracker.reset(key)
            await self.trigger(guild, user, f"Invite Spam ({limit}/{window}s)")

    # ── @everyone / @here ──────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or not message.mention_everyone:
            return
        guild, gid = message.guild, str(message.guild.id)
        if not await Database.is_module_enabled(gid, "everyone_guard"):
            return
        author = message.author
        if await self._exempt(guild, author):
            return
        if await Database.is_whitelist_user(gid, str(author.id)):
            return
        try:
            await message.delete()
        except Exception:
            pass
        await logger.log(gid, "security", "Mass mention blocked", [
            f"• User: {author.mention}  ({author.id})",
            f"• Channel: {message.channel.mention}",
            "• Message deleted.",
        ], thumb=str(author.display_avatar.url),
            event=("everyone_blocked", {"user": str(author), "id": str(author.id),
                                        "channel": getattr(message.channel, "name", "?")}))
        limit  = await Database.get_int(gid, "everyone_limit", 3)
        window = await Database.get_int(gid, "everyone_window", 60)
        key    = f"everyone_{guild.id}_{author.id}"
        if everyone_tracker.add_and_check(key, limit, window):
            everyone_tracker.reset(key)
            await self.trigger(guild, author, f"Mass @everyone ({limit}/{window}s)")

    # ── Bot added with dangerous permissions ───────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not member.bot:
            return
        guild = member.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        if await Database.is_whitelist_bot(gid, str(member.id)) or member.id == self.bot.user.id:
            return
        if not any(getattr(member.guild_permissions, p) for p in DANGEROUS_PERMS):
            return

        entry = await find_entry(guild, AA.bot_add, target_id=member.id, max_age=15)
        adder = entry.user if entry else None

        await Database.log_event(gid, "dangerous_bot_add", {
            "user": str(adder) if adder else "unknown", "id": str(adder.id) if adder else "",
            "bot": str(member),
        })

        removable = dangerous_member_roles(guild, member)
        try:
            if removable:
                await member.remove_roles(*removable, reason="Anti-Nuke: bot added with dangerous permissions")
        except Exception as e:
            print(f"[AntiNuke] dangerous bot role strip failed: {e}")

        await logger.log(gid, "security", "Dangerous bot added", [
            f"• Bot: {member.mention}  ({member.id})",
            f"• Added by: {adder.mention if adder else 'unknown'}",
            f"• Dangerous roles stripped: {len(removable)}",
            "• Review it and use `/whitelist bot` if this was intentional.",
        ], thumb=str(member.display_avatar.url))

        if adder is not None and not await self._exempt(guild, adder):
            adder_member = await self._member(guild, adder.id)
            if adder_member:
                await self.trigger(guild, adder, f"Added bot '{member}' with dangerous permissions",
                                   event="dangerous_bot_add")

    # ── Server vandalism (name/icon/vanity) ────────────────
    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild):
        gid = str(after.id)
        if not await Database.is_module_enabled(gid, "mass_action"):
            return
        changes = []
        if before.name != after.name:
            changes.append(f"Name: `{before.name}` → `{after.name}`")
        if before.icon != after.icon:
            changes.append("Icon changed")
        vanity = getattr(before, "vanity_url_code", None) != getattr(after, "vanity_url_code", None)
        if vanity:
            changes.append("Vanity URL changed")
        if not changes:
            return
        entry = await find_entry(after, AA.guild_update)
        if entry is None or entry.user is None or await self._exempt(after, entry.user):
            return
        user = entry.user
        await Database.log_event(gid, "guild_vandalism", {
            "user": str(user), "id": str(user.id), "changes": changes
        })
        await logger.log(gid, "security", "Server settings changed", [
            f"• By: {user.mention}  ({user.id})", "• Changes:", *[f"  - {c}" for c in changes],
        ], thumb=str(user.display_avatar.url), color=0xE67E22)
        if vanity:
            await self.trigger(after, user, "Vanity URL changed")

    # ── Roles: mass create / delete ────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role):
        await self._check(role.guild, module="role_action", action=AA.role_create,
                          tracker=role_action_tracker, name="role_create",
                          limit_key="role_action_limit", window_key="role_action_window",
                          label="Mass Role Create", target_id=role.id)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        await self._check(role.guild, module="role_action", action=AA.role_delete,
                          tracker=role_action_tracker, name="role_delete",
                          limit_key="role_action_limit", window_key="role_action_window",
                          label="Mass Role Delete", target_id=role.id, heal=True)

    # ── Role permission escalation ─────────────────────────
    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = after.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "role_action"):
            return
        newly = [p for p in DANGEROUS_PERMS
                 if not getattr(before.permissions, p) and getattr(after.permissions, p)]
        if not newly:
            return
        entry = await find_entry(guild, AA.role_update, target_id=after.id)
        if entry is None or entry.user is None or await self._exempt(guild, entry.user):
            return
        user = entry.user
        try:
            await after.edit(permissions=before.permissions, reason="Anti-Nuke: permission escalation reverted")
        except Exception as e:
            print(f"[AntiNuke] role revert failed: {e}")
        await self.trigger(guild, user,
                           f"Permission Escalation on '{after.name}' ({', '.join(newly)})",
                           event="perm_escalation")

    # ── Member escalation (δίνει επικίνδυνο role σε μέλος) ──
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        added = [r for r in after.roles if r not in before.roles]
        added = [r for r in added if is_dangerous(r.permissions) and not r.managed]
        if not added:
            return
        guild = after.guild
        gid   = str(guild.id)
        if not await Database.is_module_enabled(gid, "escalation_guard"):
            return
        entry = await find_entry(guild, AA.member_role_update, target_id=after.id)
        if entry is None or entry.user is None or await self._exempt(guild, entry.user):
            return
        user = entry.user
        removable = [r for r in added if below_bot(guild, r)]
        try:
            if removable:
                await after.remove_roles(*removable, reason="Anti-Nuke: unauthorized dangerous role grant reverted")
        except Exception as e:
            print(f"[AntiNuke] escalation revert failed: {e}")
        await self.trigger(guild, user,
                           f"Gave dangerous role(s) {', '.join(r.name for r in added)} to {after}",
                           event="perm_escalation")


async def setup(bot: commands.Bot):
    await bot.add_cog(AntiNuke(bot))

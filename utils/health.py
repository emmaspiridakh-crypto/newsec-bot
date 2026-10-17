"""Έλεγχος υγείας του bot ανά server (/securitycheck και Panel → Health)."""
import discord

from database import Database
from utils.security import dangerous_names

REQUIRED = {
    "view_audit_log":   "View Audit Log (needed to identify who did an action)",
    "manage_roles":     "Manage Roles (strip roles, quarantine, lockdown)",
    "manage_channels":  "Manage Channels (lockdown, backup restore)",
    "moderate_members": "Timeout Members (punishment)",
    "kick_members":     "Kick Members (bots / alt kick)",
    "ban_members":      "Ban Members",
    "manage_messages":  "Manage Messages (delete links/tokens)",
}

OK, WARN, FAIL = "OK", "WARN", "FAIL"


async def run_checks(bot, guild: discord.Guild) -> list:
    gid, out = str(guild.id), []
    me = guild.me
    perms = me.guild_permissions

    # Permissions
    if perms.administrator:
        out.append((OK, "Bot has Administrator."))
    else:
        missing = [d for p, d in REQUIRED.items() if not getattr(perms, p)]
        if missing:
            out.append((FAIL, "Missing permissions:\n" + "\n".join(f"   - {m}" for m in missing)))
        else:
            out.append((OK, "All required permissions are granted."))

    # Role hierarchy
    above = [r for r in guild.roles if r.position > me.top_role.position and not r.managed]
    if above:
        out.append((FAIL, f"Bot's top role (**{me.top_role.name}**) is below {len(above)} role(s), e.g. "
                          f"{', '.join(r.name for r in above[:3])}. Move it to the very top, otherwise "
                          "Anti-Nuke cannot punish anyone holding those roles."))
    else:
        out.append((OK, "Bot role is at the top of the hierarchy."))

    # Audit log
    try:
        _ = [e async for e in guild.audit_logs(limit=1)]
        out.append((OK, "Audit log is readable."))
    except Exception:
        out.append((FAIL, "Cannot read the audit log — Anti-Nuke and Logs cannot attribute actions."))

    # Log channel
    cid = await Database.get_setting(gid, "log_channel_id")
    ch = guild.get_channel(int(cid)) if cid else None
    if not cid:
        out.append((FAIL, "No log channel set (`/setlogchannel` or Panel → Logs). Alerts and the Restore button need it."))
    elif ch is None:
        out.append((FAIL, "The configured log channel no longer exists."))
    elif not ch.permissions_for(me).send_messages:
        out.append((FAIL, f"Bot cannot send messages in {ch.mention}."))
    else:
        out.append((OK, f"Log channel: {ch.mention}"))

    # @everyone
    bad = dangerous_names(guild.default_role.permissions)
    if bad:
        out.append((WARN, f"@everyone has dangerous permissions: {', '.join(bad)}."))
    else:
        out.append((OK, "@everyone has no dangerous permissions."))

    # Roles with administrator
    admins = [r for r in guild.roles if r.permissions.administrator and not r.managed and not r.is_default()]
    if admins:
        out.append((WARN, "Roles with Administrator: " + ", ".join(r.name for r in admins[:6])
                    + ("…" if len(admins) > 6 else "")))

    # 2FA / verification
    if guild.mfa_level == discord.MFALevel.disabled and admins:
        out.append((WARN, "2FA requirement for moderators is OFF (Server Settings → Safety Setup)."))
    if guild.verification_level in (discord.VerificationLevel.none, discord.VerificationLevel.low):
        out.append((WARN, f"Verification level is `{guild.verification_level}` — consider raising it."))

    # Bots not whitelisted
    wl = set(await Database.get_whitelist_bots(gid))
    watched = [m for m in guild.members if m.bot and m.id != bot.user.id and str(m.id) not in wl]
    if watched:
        out.append((WARN, f"{len(watched)} bot(s) are monitored by Anti-Nuke/AutoMod (not whitelisted): "
                          + ", ".join(m.name for m in watched[:6]) + ("…" if len(watched) > 6 else "")
                          + "\n   Use `/whitelist bot` for the ones you trust (ticket bots, etc.)."))

    # Backup
    last = await Database.latest_backup(gid, kinds=("auto", "manual"))
    if last:
        out.append((OK, f"Last backup: <t:{last['created']}:R> (#{last['id']})"))
    else:
        out.append((WARN, "No backup yet — run `/backup create`."))

    # Lockdown
    ld = await Database.get_lockdown(gid)
    if ld:
        out.append((WARN, f"Lockdown is ACTIVE since <t:{ld['started']}:R> ({ld['kind']})."))

    # Disabled modules
    dis = await Database.get_disabled_modules(gid)
    if dis:
        out.append((WARN, "Disabled modules: " + ", ".join(dis)))

    return out


def render(results: list) -> str:
    lines = []
    for level, text in results:
        lines.append(f"**[{level}]** {text}")
    return "\n".join(lines)

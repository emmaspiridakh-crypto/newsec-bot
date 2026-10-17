"""Quarantine role: κλειδώνει ένα μέλος όταν το timeout δεν είναι δυνατό."""
import asyncio

import discord

from database import Database
from utils.security import below_bot

ROLE_NAME = "Quarantined"
_DENY = dict(view_channel=False, send_messages=False, connect=False, add_reactions=False,
             send_messages_in_threads=False, create_public_threads=False, speak=False)


async def ensure_role(guild: discord.Guild) -> discord.Role | None:
    gid = str(guild.id)
    rid = await Database.get_setting(gid, "quarantine_role_id")
    role = guild.get_role(int(rid)) if rid else None
    if role:
        return role
    try:
        role = await guild.create_role(
            name=ROLE_NAME, permissions=discord.Permissions.none(),
            reason="Security bot: quarantine role"
        )
    except Exception as e:
        print(f"[Quarantine] create role failed: {e}")
        return None
    await Database.set_setting(gid, "quarantine_role_id", str(role.id))
    ow = discord.PermissionOverwrite(**_DENY)
    await asyncio.gather(
        *[ch.set_permissions(role, overwrite=ow, reason="Quarantine role setup")
          for ch in guild.channels],
        return_exceptions=True,
    )
    return role


async def protect_channel(guild: discord.Guild, channel):
    """Κάθε νέο κανάλι παίρνει deny για το quarantine role."""
    rid = await Database.get_setting(str(guild.id), "quarantine_role_id")
    role = guild.get_role(int(rid)) if rid else None
    if role:
        try:
            await channel.set_permissions(role, overwrite=discord.PermissionOverwrite(**_DENY),
                                          reason="Quarantine role setup")
        except Exception:
            pass


async def apply(guild: discord.Guild, member: discord.Member, reason: str,
                strip: bool = True) -> dict:
    """Βάζει το quarantine role και (προαιρετικά) αφαιρεί τα υπόλοιπα roles."""
    res = {"ok": False, "stripped": []}
    role = await ensure_role(guild)
    if role is None:
        return res
    removable = []
    if strip:
        removable = [r for r in member.roles
                     if not r.is_default() and not r.managed and r != role and below_bot(guild, r)]
    try:
        if removable:
            await member.remove_roles(*removable, reason=f"Quarantine: {reason}")
        await member.add_roles(role, reason=f"Quarantine: {reason}")
        res["ok"] = True
        res["stripped"] = removable
    except Exception as e:
        print(f"[Quarantine] apply failed: {e}")
        return res
    if removable:
        await Database.save_quarantine(str(guild.id), str(member.id),
                                       [r.id for r in removable], reason)
    return res


async def release(guild: discord.Guild, member: discord.Member) -> dict:
    gid = str(guild.id)
    rec = await Database.get_quarantine(gid, str(member.id))
    out = {"restored": [], "timeout_cleared": False, "role_removed": False}
    rid = await Database.get_setting(gid, "quarantine_role_id")
    qrole = guild.get_role(int(rid)) if rid else None
    try:
        if qrole and qrole in member.roles:
            await member.remove_roles(qrole, reason="Quarantine released")
            out["role_removed"] = True
    except Exception as e:
        print(f"[Quarantine] role remove failed: {e}")
    if rec:
        roles = [guild.get_role(r) for r in rec["roles"]]
        roles = [r for r in roles if r and below_bot(guild, r) and r not in member.roles]
        try:
            if roles:
                await member.add_roles(*roles, reason="Quarantine released")
            out["restored"] = roles
        except Exception as e:
            print(f"[Quarantine] role restore failed: {e}")
    if member.is_timed_out():
        try:
            await member.timeout(None, reason="Quarantine released")
            out["timeout_cleared"] = True
        except Exception:
            pass
    await Database.delete_quarantine(gid, str(member.id))
    return out

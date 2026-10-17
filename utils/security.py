import re

import discord

DANGEROUS_PERMS = (
    "administrator", "ban_members", "kick_members", "manage_guild",
    "manage_roles", "manage_channels", "manage_webhooks",
    "mention_everyone", "manage_messages",
)


def dangerous_names(perms: discord.Permissions) -> list:
    return [p for p in DANGEROUS_PERMS if getattr(perms, p)]


def is_dangerous(perms: discord.Permissions) -> bool:
    return any(getattr(perms, p) for p in DANGEROUS_PERMS)


def strip_dangerous(perms: discord.Permissions) -> discord.Permissions:
    p = discord.Permissions(perms.value)
    for name in DANGEROUS_PERMS:
        setattr(p, name, False)
    return p


def below_bot(guild: discord.Guild, role: discord.Role) -> bool:
    """True αν το bot μπορεί (ιεραρχικά) να πειράξει το role."""
    return role.position < guild.me.top_role.position


def dangerous_member_roles(guild: discord.Guild, member: discord.Member) -> list:
    """Roles του μέλους με επικίνδυνα permissions που το bot μπορεί να αφαιρέσει."""
    return [
        r for r in member.roles
        if not r.is_default() and not r.managed
        and below_bot(guild, r) and is_dangerous(r.permissions)
    ]


_PAIR = re.compile(r"^\s*(\d{1,4})\s*[/,x ]\s*(\d{1,6})\s*$")


def parse_pair(text: str):
    """'3/10' -> (3, 10). None αν δεν είναι έγκυρο ή αν κάποιο είναι 0."""
    m = _PAIR.match(text or "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a < 1 or b < 1:
        return None
    return a, b

import asyncio
import time

import discord

_CACHE: dict = {}
_CACHE_TTL = 0.5


async def find_entry(guild: discord.Guild, action, *, target_id: int = None,
                     max_age: float = 10.0, delay: float = 0.6, limit: int = 8):
    """Βρίσκει το πρόσφατο audit log entry για μια ενέργεια.
    Ελέγχει target και χρόνο ώστε να μην αντιστοιχίζεται λάθος άτομο.
    Επιστρέφει None αν δεν βρεθεί ή αν το bot δεν έχει View Audit Log."""
    await asyncio.sleep(delay)
    key = (guild.id, action)
    now = time.time()
    cached = _CACHE.get(key)
    if cached and now - cached[0] < _CACHE_TTL:
        entries = cached[1]
    else:
        try:
            entries = [e async for e in guild.audit_logs(limit=limit, action=action)]
        except (discord.Forbidden, discord.HTTPException):
            return None
        _CACHE[key] = (now, entries)
        if len(_CACHE) > 200:
            for k in [k for k, v in _CACHE.items() if now - v[0] > 5]:
                _CACHE.pop(k, None)

    for entry in entries:
        age = (discord.utils.utcnow() - entry.created_at).total_seconds()
        if age > max_age:
            continue
        if target_id is not None:
            if entry.target is None or getattr(entry.target, "id", None) != target_id:
                continue
        return entry
    return None

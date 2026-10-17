import time

MISSING = object()


class TTLCache:
    """Μικρό in-memory cache με λήξη. Κλειδιά: (guild_id, namespace)."""

    def __init__(self, ttl: float = 600.0):
        self.ttl   = ttl
        self._data = {}

    def get(self, key, default=MISSING):
        item = self._data.get(key)
        if item is None:
            return default
        expires, value = item
        if expires < time.time():
            self._data.pop(key, None)
            return default
        return value

    def set(self, key, value):
        self._data[key] = (time.time() + self.ttl, value)

    def delete(self, key):
        self._data.pop(key, None)

    def delete_guild(self, guild_id: str):
        for k in [k for k in self._data if k[0] == guild_id]:
            self._data.pop(k, None)

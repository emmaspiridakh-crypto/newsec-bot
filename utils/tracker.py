import time
from collections import defaultdict


class ActionTracker:
    """Sliding-window action counter for spam / anti-nuke detection."""

    _instances: list = []

    def __init__(self):
        self._data: dict[str, list[float]] = defaultdict(list)
        ActionTracker._instances.append(self)

    def add_and_check(self, key: str, threshold: int, window_secs: int) -> bool:
        """Add one action and return True if threshold is reached within window."""
        now = time.time()
        cutoff = now - window_secs
        self._data[key] = [t for t in self._data[key] if t > cutoff]
        self._data[key].append(now)
        return len(self._data[key]) >= threshold

    def reset(self, key: str):
        self._data.pop(key, None)

    def count(self, key: str, window_secs: int) -> int:
        now = time.time()
        cutoff = now - window_secs
        self._data[key] = [t for t in self._data[key] if t > cutoff]
        return len(self._data[key])

    def sweep(self, max_age: float = 600.0):
        """Σβήνει κλειδιά που δεν έχουν πρόσφατη δραστηριότητα (αποτρέπει memory leak)."""
        cutoff = time.time() - max_age
        for k in [k for k, v in self._data.items() if not v or v[-1] < cutoff]:
            self._data.pop(k, None)

    @classmethod
    def sweep_all(cls, max_age: float = 600.0):
        for inst in cls._instances:
            inst.sweep(max_age)


# Global trackers — imported wherever needed
spam_tracker           = ActionTracker()
ban_tracker            = ActionTracker()
kick_tracker           = ActionTracker()
channel_del_tracker    = ActionTracker()
role_action_tracker    = ActionTracker()
role_tracker           = ActionTracker()
webhook_tracker        = ActionTracker()
channel_create_tracker = ActionTracker()
channel_edit_tracker   = ActionTracker()
emoji_tracker          = ActionTracker()
invite_tracker         = ActionTracker()
purge_tracker          = ActionTracker()
everyone_tracker       = ActionTracker()
leave_tracker          = ActionTracker()

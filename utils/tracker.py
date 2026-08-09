import time
from collections import defaultdict


class ActionTracker:
    """Sliding-window action counter for spam / anti-nuke detection."""

    def __init__(self):
        self._data: dict[str, list[float]] = defaultdict(list)

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


# Global trackers — imported wherever needed
spam_tracker          = ActionTracker()
ban_tracker           = ActionTracker()
kick_tracker          = ActionTracker()
channel_del_tracker   = ActionTracker()
role_action_tracker   = ActionTracker()
role_tracker          = ActionTracker()
webhook_tracker       = ActionTracker()

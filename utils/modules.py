"""Κεντρικό registry των modules — το χρησιμοποιούν το panel, το /status και το /securitycheck."""

# key -> (όνομα στο panel, κατηγορία)
MODULES = {
    "mass_action":         ("Mass Action",   "anti"),
    "role_action":         ("Role Guard",    "anti"),
    "channel_guard":       ("Channel Guard", "anti"),
    "emoji_guard":         ("Emoji Guard",   "anti"),
    "purge_guard":         ("Purge Guard",   "anti"),
    "invite_guard":        ("Invite Guard",  "anti"),
    "everyone_guard":      ("Everyone Guard", "anti"),
    "escalation_guard":    ("Escalation Guard", "anti"),
    "link":                ("Link Filter",   "automod"),
    "token":               ("Token Filter",  "automod"),
    "spam":                ("Spam Filter",   "automod"),
    "automod_bots":        ("Bot Scan",      "automod"),
    "alt":                 ("ALT Detection", "alt"),
    "bot_verify":          ("Bot Verify",    "alt"),
    "auto_backup":         ("Auto Backup",   "backup"),
    "auto_restore":        ("Auto Restore",  "backup"),
    "log_members":         ("Members",       "logs"),
    "log_server":          ("Server",        "logs"),
    "log_security":        ("Security",      "logs"),
    "log_bot":             ("Bot",           "logs"),
}

CATEGORIES = {
    "anti":    "Anti-Nuke",
    "automod": "AutoMod",
    "alt":     "ALT & Bots",
    "backup":  "Backup",
    "logs":    "Logs",
}


def modules_in(category: str) -> list:
    return [k for k, (_, c) in MODULES.items() if c == category]

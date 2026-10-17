"""Μικροί builders για Components V2 (raw JSON)."""

CV2_TEXT_LIMIT = 3800


def text(content: str) -> dict:
    return {"type": 10, "content": content[:CV2_TEXT_LIMIT]}


def sep() -> dict:
    return {"type": 14}


def btn(label: str, cid: str, style: int = 2, disabled: bool = False) -> dict:
    return {"type": 2, "label": label[:80], "style": style,
            "custom_id": cid[:100], "disabled": disabled}


def row(*items) -> dict:
    return {"type": 1, "components": list(items)}


def container(children: list, color: int = 0x5865F2) -> dict:
    return {"type": 17, "accent_color": color, "components": children}


def header(content: str, thumb: str | None = None) -> dict:
    if thumb:
        return {"type": 9, "components": [text(content)],
                "accessory": {"type": 11, "media": {"url": thumb}}}
    return text(content)


def string_select(cid: str, options: list, placeholder: str) -> dict:
    return {"type": 3, "custom_id": cid, "placeholder": placeholder[:100],
            "min_values": 1, "max_values": 1, "options": options[:25]}


def channel_select(cid: str, placeholder: str) -> dict:
    return {"type": 8, "custom_id": cid, "placeholder": placeholder[:100],
            "min_values": 1, "max_values": 1, "channel_types": [0]}


def count_components(items) -> int:
    n = 0
    for c in items:
        n += 1
        if "components" in c:
            n += count_components(c["components"])
        if "accessory" in c:
            n += 1
    return n


def restore_confirm(guild_id, backup_id, info: dict) -> list:
    """Σελίδα επιβεβαίωσης πριν από οποιοδήποτε backup restore."""
    return [container([
        text(
            "> Confirm Backup Restore\n"
            f"• Backup: **#{backup_id}**  ({info.get('kind', '?')})\n"
            f"• Created: <t:{info.get('created', 0)}:F>\n"
            f"• Roles: {info.get('roles', '?')}  •  Channels: {info.get('channels', '?')}\n\n"
            "This recreates missing roles/channels and re-applies the saved role "
            "permissions and channel overwrites. Nothing gets deleted.\n"
            "Continue?"
        ),
        sep(),
        row(btn("Confirm Restore", f"bk_confirm_{guild_id}_{backup_id}", 4),
            btn("Cancel", f"bk_cancel_{guild_id}", 2)),
    ], 0xE67E22)]

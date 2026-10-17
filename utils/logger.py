"""Ενιαίο logging σε ΕΝΑ κανάλι με 4 κατηγορίες: Members, Server, Security, Bot."""
import asyncio
import os

from database import Database
from utils.cv2_helper import get_session
from utils import ui

CATEGORIES = {
    "members":  ("MEMBERS",  0x5865F2, "log_members"),
    "server":   ("SERVER",   0xFEE75C, "log_server"),
    "security": ("SECURITY", 0x8B0000, "log_security"),
    "bot":      ("BOT",      0x57F287, "log_bot"),
}

_queue: asyncio.Queue | None = None
_worker: asyncio.Task | None = None


async def _post(channel_id: int, components: list) -> dict | None:
    headers = {"Authorization": f"Bot {os.getenv('TOKEN')}", "Content-Type": "application/json"}
    url     = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    for _ in range(4):
        try:
            async with get_session().post(
                url, json={"flags": 1 << 15, "components": components}, headers=headers
            ) as r:
                if r.status == 429:
                    try:
                        wait = float((await r.json()).get("retry_after", 1))
                    except Exception:
                        wait = 1.0
                    await asyncio.sleep(min(wait, 10) + 0.1)
                    continue
                if r.status not in (200, 201, 204):
                    print(f"[Logger] {r.status} {await r.text()}")
                    return None
                try:
                    return await r.json()
                except Exception:
                    return None
        except Exception as e:
            print(f"[Logger] post failed: {type(e).__name__}: {e}")
            return None
    return None


async def _run_worker():
    while True:
        channel_id, components = await _queue.get()
        try:
            await _post(channel_id, components)
        except Exception as e:
            print(f"[Logger] worker error: {e}")
        await asyncio.sleep(0.35)


def _ensure_worker():
    global _queue, _worker
    if _queue is None:
        _queue = asyncio.Queue(maxsize=500)
    if _worker is None or _worker.done():
        _worker = asyncio.create_task(_run_worker())


def build(category: str, title: str, lines: list, *, thumb: str = None,
          color: int = None, extra: list = None) -> list:
    label, default_color, _ = CATEGORIES[category]
    body = f"> **{label}** · {title}\n" + "\n".join(lines)
    children = [ui.header(body, thumb)]
    if extra:
        children.append(ui.sep())
        children.extend(extra)
    return [ui.container(children, color or default_color)]


async def log(guild_id, category: str, title: str, lines: list, *, thumb: str = None,
              color: int = None, extra: list = None, urgent: bool = False,
              event: tuple = None):
    """Στέλνει log. urgent=True → άμεση αποστολή και επιστροφή του message json.
    event=(type, data) → γράφει και στο ιστορικό events (/logs)."""
    gid = str(guild_id)
    if event:
        try:
            await Database.log_event(gid, event[0], event[1])
        except Exception as e:
            print(f"[Logger] event save failed: {e}")

    _, _, module = CATEGORIES[category]
    if not await Database.is_module_enabled(gid, module):
        return None
    cid = await Database.get_setting(gid, "log_channel_id")
    if not cid:
        return None

    components = build(category, title, lines, thumb=thumb, color=color, extra=extra)
    if urgent:
        return await _post(int(cid), components)
    _ensure_worker()
    try:
        _queue.put_nowait((int(cid), components))
    except asyncio.QueueFull:
        print("[Logger] queue full — log dropped")
    return None


async def edit(channel_id: int, message_id: int, components: list):
    headers = {"Authorization": f"Bot {os.getenv('TOKEN')}", "Content-Type": "application/json"}
    try:
        async with get_session().patch(
            f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
            json={"flags": 1 << 15, "components": components}, headers=headers
        ) as r:
            if r.status not in (200, 204):
                print(f"[Logger:edit] {r.status} {await r.text()}")
    except Exception as e:
        print(f"[Logger:edit] failed: {e}")

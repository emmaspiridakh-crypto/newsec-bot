import aiohttp
import os

TOKEN = os.getenv("TOKEN")

_session: aiohttp.ClientSession | None = None
_TIMEOUT = aiohttp.ClientTimeout(total=15)


def get_session() -> aiohttp.ClientSession:
    """Επιστρέφει ένα shared aiohttp session (δημιουργείται μία φορά).
    Πριν έφτιαχνε καινούριο ClientSession (νέο TCP+TLS handshake) σε ΚΑΘΕ
    κλήση, κάτι που έκανε αργά όλα τα panels (πιο αισθητό στο /allservers
    λόγω των πολλών page-navigation κλικ). Έχει timeout=15s ώστε ένα
    κολλημένο request να αποτυγχάνει γρήγορα αντί να κρεμάει για πάντα
    (το 'is thinking...' που δεν έληγε ποτέ)."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=_TIMEOUT)
    return _session


async def close_session():
    """Κλείνει το shared session — κάλεσέ το στο shutdown του bot."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None


async def respond_cv2(interaction, components: list, ephemeral: bool = False):
    """Direct response to a fresh interaction (type 4). Χρησιμοποιείται για buttons."""
    flags = 1 << 15
    if ephemeral:
        flags |= 1 << 6
    payload = {"type": 4, "data": {"flags": flags, "components": components}}
    try:
        async with get_session().post(
            f"https://discord.com/api/v10/interactions/{interaction.id}/{interaction.token}/callback",
            json=payload
        ) as r:
            if r.status not in (200, 204):
                print(f"[CV2:respond] {r.status} {await r.text()}")
    except Exception as e:
        print(f"[CV2:respond] EXCEPTION: {type(e).__name__}: {e}")


async def update_cv2(interaction, components: list):
    """Update the original message from a button (type 7)."""
    payload = {"type": 7, "data": {"flags": 1 << 15, "components": components}}
    try:
        async with get_session().post(
            f"https://discord.com/api/v10/interactions/{interaction.id}/{interaction.token}/callback",
            json=payload
        ) as r:
            if r.status not in (200, 204):
                print(f"[CV2:update] {r.status} {await r.text()}")
    except Exception as e:
        print(f"[CV2:update] EXCEPTION: {type(e).__name__}: {e}")


async def edit_original_cv2(interaction, components: list, ephemeral: bool = False):
    """Αντικαθιστά το deferred 'thinking...' μήνυμα με το πραγματικό CV2 content.
    Χρησιμοποιείται ΠΑΝΤΑ μετά από interaction.response.defer()."""
    flags = 1 << 15
    if ephemeral:
        flags |= 1 << 6
    try:
        async with get_session().patch(
            f"https://discord.com/api/v10/webhooks/{interaction.application_id}/{interaction.token}/messages/@original",
            json={"flags": flags, "components": components}
        ) as r:
            if r.status not in (200, 204):
                print(f"[CV2:edit_original] {r.status} {await r.text()}")
    except Exception as e:
        print(f"[CV2:edit_original] EXCEPTION: {type(e).__name__}: {e}")


async def followup_cv2(interaction, components: list, ephemeral: bool = False):
    """Στέλνει νέο followup μήνυμα μετά από deferred interaction."""
    flags = 1 << 15
    if ephemeral:
        flags |= 1 << 6
    async with get_session().post(
        f"https://discord.com/api/v10/webhooks/{interaction.application_id}/{interaction.token}",
        json={"flags": flags, "components": components}
    ) as r:
        if r.status not in (200, 204):
            print(f"[CV2:followup] {r.status} {await r.text()}")


async def send_cv2(channel_id: int, components: list) -> dict | None:
    """Στέλνει CV2 μήνυμα σε channel (για logs)."""
    headers = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}
    async with get_session().post(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        json={"flags": 1 << 15, "components": components},
        headers=headers
    ) as r:
        if r.status not in (200, 204):
            print(f"[CV2:send] {r.status} {await r.text()}")
            return None
        try:
            return await r.json()
        except Exception:
            return None


async def edit_cv2(channel_id: int, message_id: int, components: list):
    """Επεξεργάζεται υπάρχον channel message."""
    headers = {"Authorization": f"Bot {TOKEN}", "Content-Type": "application/json"}
    async with get_session().patch(
        f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}",
        json={"flags": 1 << 15, "components": components},
        headers=headers
    ) as r:
        if r.status not in (200, 204):
            print(f"[CV2:edit] {r.status} {await r.text()}")


async def no_access(interaction, msg: str = "Δεν έχεις δικαίωμα για αυτή την εντολή."):
    """Στέλνει Access Denied. Αυτόματα χρησιμοποιεί edit_original αν έχει ήδη γίνει defer,
    αλλιώς στέλνει direct respond."""
    components = [{
        "type": 17, "accent_color": 0xED4245,
        "components": [{"type": 10, "content": f"> Access Denied\n{msg}"}]
    }]
    if interaction.response.is_done():
        await edit_original_cv2(interaction, components, ephemeral=True)
    else:
        await respond_cv2(interaction, components, ephemeral=True)

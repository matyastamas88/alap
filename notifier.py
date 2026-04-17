"""
Telegram értesítési modul — label támogatással
Mindkét csoport botja használja.
"""

import asyncio
import logging
from telegram import Bot
from config import NOTIFY_BOT_TOKEN, NOTIFY_CHAT_ID

logger = logging.getLogger(__name__)


async def send_notification(text: str):
    bot = Bot(token=NOTIFY_BOT_TOKEN)
    await bot.send_message(chat_id=NOTIFY_CHAT_ID, text=text, parse_mode="HTML")


async def notify_trade_opened(deal: dict, label: str = ""):
    """
    Értesítés pozíció megnyitásról.
    Megkülönbözteti az azonnali piaci belépést a limit megbízástól.
    """
    forras = f"\nForrás: <b>{label}</b>" if label else ""
    action = deal["action"]
    emoji_irany = "📈" if action == "BUY" else "📉"

    is_market = deal.get("is_market", False)

    if is_market:
        # Azonnali piaci belépés
        emoji = "⚡"
        tipus = "Azonnali belépés!"
    else:
        # Limit megbízás teljesült (pending → active)
        emoji = "✅"
        tipus = "Limit megbízás teljesült!"

    msg = (
        f"{emoji} <b>{tipus}</b>{forras}\n\n"
        f"Irány: <b>{action}</b> {emoji_irany}\n"
        f"Ár: <b>{deal['price']}</b>\n"
        f"Lot: {deal['lot']}\n"
        f"SL: {deal['sl']} | TP: {deal['tp']}\n"
        f"Ticket: #{deal['ticket']} | Magic: {deal.get('magic', '?')}"
    )
    await send_notification(msg)


async def notify_pending_opened(deal: dict, label: str = ""):
    """
    Értesítés függő (limit) megbízás nyitásáról.
    """
    forras = f"\nForrás: <b>{label}</b>" if label else ""
    action = deal["action"]
    emoji_irany = "📈" if action == "BUY" else "📉"

    msg = (
        f"⏳ <b>Függő megbízás nyitva</b>{forras}\n\n"
        f"Irány: <b>{action}</b> {emoji_irany}\n"
        f"Entry: <b>{deal['price']}</b>\n"
        f"Lot: {deal['lot']}\n"
        f"SL: {deal['sl']} | TP: {deal['tp']}\n"
        f"Ticket: #{deal['ticket']} | Magic: {deal.get('magic', '?')}\n\n"
        f"⏰ Automatikusan törlődik 30 perc után ha nem teljesül."
    )
    await send_notification(msg)


async def notify_trade_failed(reason: str, label: str = ""):
    forras = f"\nForrás: <b>{label}</b>" if label else ""
    await send_notification(
        f"⚠️ <b>Megbízás sikertelen</b>{forras}\n\nOk: {reason}"
    )

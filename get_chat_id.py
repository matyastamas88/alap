"""
Segédprogram: Telegram csatorna ID megkeresése
Futtasd ezt egyszer, hogy megtudd a csatorna pontos ID-ját.

Futtatás: python get_chat_id.py
"""

import asyncio
from telethon import TelegramClient
import config


async def main():
    print("Telegram csatornák és csoportok listázása...\n")

    client = TelegramClient("xauusd_bot_session", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    await client.start(phone=config.TELEGRAM_PHONE)

    async for dialog in client.iter_dialogs():
        print(f"Név: {dialog.name:<40} | ID: {dialog.id}")

    await client.disconnect()
    print("\nKeresd meg a listában a jelzések csatornáját, és másold be az ID-t a config.py SIGNAL_CHANNEL mezőjébe.")


if __name__ == "__main__":
    asyncio.run(main())

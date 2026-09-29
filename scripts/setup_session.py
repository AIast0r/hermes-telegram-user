"""Interactive helper that prints a Telethon StringSession.

Run outside Hermes:
    HERMES_TG_USER_API_ID=... HERMES_TG_USER_API_HASH=... python scripts/setup_session.py
"""
from __future__ import annotations

import asyncio
import os


async def main() -> None:
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(os.environ["HERMES_TG_USER_API_ID"])
    api_hash = os.environ["HERMES_TG_USER_API_HASH"]
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start()
    print("\nHERMES_TG_USER_SESSION=")
    print(client.session.save())
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

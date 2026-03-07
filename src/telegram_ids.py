from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from telegram import Bot, Chat, Update


@dataclass(frozen=True)
class ChatCandidate:
    chat_id: int
    chat_type: str
    title: str


def _chat_title(chat: Chat) -> str:
    if chat.title:
        return chat.title
    full_name = " ".join([part for part in [chat.first_name, chat.last_name] if part])
    if full_name:
        return full_name
    return chat.username or "unknown"


def _extract_chat(update: Update) -> Chat | None:
    if update.message and update.message.chat:
        return update.message.chat
    if update.channel_post and update.channel_post.chat:
        return update.channel_post.chat
    if update.edited_channel_post and update.edited_channel_post.chat:
        return update.edited_channel_post.chat
    if update.my_chat_member and update.my_chat_member.chat:
        return update.my_chat_member.chat
    return None


def _collect_candidates(updates: list[Update]) -> tuple[list[ChatCandidate], list[ChatCandidate]]:
    admin_map: dict[int, ChatCandidate] = {}
    channel_map: dict[int, ChatCandidate] = {}

    for update in updates:
        chat = _extract_chat(update)
        if not chat:
            continue
        item = ChatCandidate(chat_id=chat.id, chat_type=chat.type, title=_chat_title(chat))
        if chat.type in ("private", "group", "supergroup"):
            admin_map[chat.id] = item
        if chat.type == "channel":
            channel_map[chat.id] = item

    return list(admin_map.values()), list(channel_map.values())


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Подсказка для TELEGRAM_ADMIN_CHAT_ID и TELEGRAM_CHANNEL_ID"
    )
    parser.add_argument(
        "--channel",
        help="Проверить конкретный канал через getChat (пример: @my_channel или -100123...)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Сколько update запрашивать из Telegram (по умолчанию: 100)",
    )
    args = parser.parse_args()

    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Missing TELEGRAM_BOT_TOKEN in .env")

    bot = Bot(token=token)
    me = await bot.get_me()
    print(f"Bot: @{me.username} ({me.id})")
    print()

    updates = await bot.get_updates(
        limit=max(1, min(args.limit, 100)),
        timeout=0,
    )
    admin_candidates, channel_candidates = _collect_candidates(updates)

    print("ADMIN_CHAT_ID candidates:")
    if admin_candidates:
        for item in sorted(admin_candidates, key=lambda x: x.chat_id):
            print(f"- {item.chat_id} [{item.chat_type}] {item.title}")
    else:
        print("- Не найдено. Напишите боту /start из нужного аккаунта/группы и запустите снова.")
    print()

    print("CHANNEL_ID candidates from updates:")
    if channel_candidates:
        for item in sorted(channel_candidates, key=lambda x: x.chat_id):
            print(f"- {item.chat_id} [{item.chat_type}] {item.title}")
    else:
        print("- Не найдено в updates. Добавьте бота в канал и дайте право публикации.")
    print()

    if args.channel:
        chat = await bot.get_chat(args.channel)
        print("CHANNEL_ID from --channel:")
        print(f"- {chat.id} [{chat.type}] {_chat_title(chat)}")
        print()

    print("Готово. Скопируйте значения в .env:")
    print("TELEGRAM_ADMIN_CHAT_ID=<id>")
    print("TELEGRAM_CHANNEL_ID=<id или @username>")


if __name__ == "__main__":
    asyncio.run(main())

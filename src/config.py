from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_channel_id: str
    telegram_test_mode: bool
    telegram_test_channel_id: str
    telegram_admin_chat_id: int
    llm_provider: str
    openai_api_key: str
    llm_model: str
    rss_feeds: list[str]
    poll_interval_minutes: int
    max_items_per_poll: int
    state_file: str


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def load_settings() -> Settings:
    load_dotenv()

    feeds_raw = os.getenv("RSS_FEEDS", "https://www.helpnetsecurity.com/feed/").strip()
    rss_feeds = [f.strip() for f in feeds_raw.split(",") if f.strip()]
    run_mode = os.getenv("NEWS_BOT_MODE", "prod").strip().lower()
    if run_mode not in {"prod", "test"}:
        raise ValueError("NEWS_BOT_MODE must be either 'prod' or 'test'")
    telegram_test_mode = run_mode == "test"
    telegram_test_channel_id = os.getenv("TELEGRAM_TEST_CHANNEL_ID", "").strip()
    if telegram_test_mode and not telegram_test_channel_id:
        raise ValueError("Missing required environment variable: TELEGRAM_TEST_CHANNEL_ID")

    return Settings(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_channel_id=_required("TELEGRAM_CHANNEL_ID"),
        telegram_test_mode=telegram_test_mode,
        telegram_test_channel_id=telegram_test_channel_id,
        telegram_admin_chat_id=int(_required("TELEGRAM_ADMIN_CHAT_ID")),
        llm_provider=os.getenv("LLM_PROVIDER", "openai").strip().lower(),
        openai_api_key=_required("OPENAI_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4o-mini").strip(),
        rss_feeds=rss_feeds,
        poll_interval_minutes=int(os.getenv("POLL_INTERVAL_MINUTES", "10")),
        max_items_per_poll=int(os.getenv("MAX_ITEMS_PER_POLL", "3")),
        state_file=os.getenv("STATE_FILE", "state.json").strip(),
    )

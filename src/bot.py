from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import html
import logging
import re
import uuid
from typing import Any

import feedparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.article_fetcher import USER_AGENT, fetch_article_text
from src.config import Settings, load_settings
from src.llm import LLMClient
from src.storage import StateStore


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TAG_RE = re.compile(r"<[^>]+>")
ADMIN_BOT_ID_ERROR = "bots can't send messages to bots"
MAX_TG_TEXT_LEN = 4096
MAX_RAW_PREVIEW_SUMMARY_LEN = 260
REQUIRED_FEED_CATEGORY = "Industry news"
RAW_REVIEW_STAGE = "raw_review"
FINAL_REVIEW_STAGE = "final_review"


def resolve_publish_channel(settings: Settings) -> tuple[str, str]:
    if settings.telegram_test_mode:
        return settings.telegram_test_channel_id, "TELEGRAM_TEST_CHANNEL_ID"
    return settings.telegram_channel_id, "TELEGRAM_CHANNEL_ID"


def strip_html(text: str) -> str:
    raw = TAG_RE.sub(" ", text or "")
    raw = html.unescape(raw)
    raw = re.sub(
        r"\bMore\s*[→›»>]+\s*The post .*? appeared first on .*?\.?\s*$",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    raw = re.sub(
        r"\bThe post .*? appeared first on .*?\.?\s*$",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", " ", raw).strip()


def raw_review_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Принять в работу", callback_data=f"apr:{draft_id}")],
            [InlineKeyboardButton("Отклонить", callback_data=f"rej:{draft_id}")],
        ]
    )


def final_review_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Опубликовать", callback_data=f"pub:{draft_id}")],
            [InlineKeyboardButton("Переписать", callback_data=f"rew:{draft_id}")],
            [InlineKeyboardButton("Отклонить", callback_data=f"rej:{draft_id}")],
        ]
    )


def build_raw_news_body(title: str, summary: str) -> str:
    summary_short = summary.strip()
    parts = []
    title_clean = title.strip()
    if title_clean:
        parts.append(title_clean)
    if summary_short:
        parts.append(summary_short)
    return "\n\n".join(parts).strip()


def build_feed_preview_summary(summary: str, max_len: int = MAX_RAW_PREVIEW_SUMMARY_LEN) -> str:
    value = summary.strip()
    if len(value) <= max_len:
        return value
    cropped = value[:max_len].rsplit(" ", 1)[0].strip()
    if not cropped:
        cropped = value[:max_len].strip()
    return cropped.rstrip(" .,:;!?") + "..."


def extract_first_paragraph(text: str) -> str:
    for paragraph in text.splitlines():
        value = paragraph.strip()
        if value:
            return value
    return ""


def trim_telegram_text(text: str, max_len: int = MAX_TG_TEXT_LEN) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def extract_preview_text_from_feed(entry: Any) -> str:
    description = str(getattr(entry, "description", "") or entry.get("description", "")).strip()
    if description:
        logger.info("Preview source: RSS description")
        return strip_html(description)
    logger.info("Preview source fallback: RSS summary")
    return strip_html(str(getattr(entry, "summary", "") or entry.get("summary", "")).strip())


def extract_extended_text_from_feed(entry: Any) -> str:
    entry_content = getattr(entry, "content", None)
    if not isinstance(entry_content, list):
        return ""

    parts: list[str] = []
    for chunk in entry_content:
        if not isinstance(chunk, dict):
            continue
        value = chunk.get("value", "")
        if isinstance(value, str) and value.strip():
            parts.append(strip_html(value))
    return " ".join([part for part in parts if part]).strip()


def entry_categories(entry: Any) -> list[str]:
    values: list[str] = []
    tags = getattr(entry, "tags", None)
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, dict):
                term = str(tag.get("term", "") or tag.get("label", "")).strip()
                if term:
                    values.append(term)
    category = str(getattr(entry, "category", "") or entry.get("category", "")).strip()
    if category:
        values.append(category)
    return values


def has_required_category(entry: Any, required: str = REQUIRED_FEED_CATEGORY) -> bool:
    required_norm = required.strip().lower()
    if not required_norm:
        return True
    return any(cat.strip().lower() == required_norm for cat in entry_categories(entry))


def build_raw_admin_text(title: str, summary: str, source_url: str, feed_published_at: str = "") -> str:
    body = build_raw_news_body(title=title, summary=summary)
    parts = [body] if body else []
    if feed_published_at.strip():
        parts.append(f"Дата новости: {feed_published_at.strip()}")
    parts.append(f"Источник: {source_url}")
    return trim_telegram_text("\n\n".join(parts))


def format_feed_published_at(entry: Any) -> str:
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed:
        try:
            # Feedparser parsed dates are UTC.
            return dt.datetime(*parsed[:6], tzinfo=dt.UTC).strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            pass
    raw_value = str(
        getattr(entry, "published", "")
        or getattr(entry, "updated", "")
        or getattr(entry, "pubDate", "")
    ).strip()
    return raw_value


def build_final_admin_text(
    title: str,
    summary: str,
    translated_text: str,
    source_url: str,
    llm_text_source: str = "",
) -> str:
    raw_body = build_raw_news_body(title=title, summary=summary)
    translated = translated_text.strip()
    parts = []
    if raw_body:
        parts.append(raw_body)
    if translated:
        parts.append(f"Предлагаемый перевод:\n\n{translated}")
    if llm_text_source:
        parts.append(f"Источник текста для перевода: {llm_text_source}")
    parts.append(f"Источник: {source_url}")
    return trim_telegram_text("\n\n".join(parts))


def build_pending_payload(
    *,
    link: str,
    title: str,
    summary: str,
    feed_published_at: str,
    raw_preview_summary: str,
    feed_text_extended: str,
    raw_admin_text: str,
    admin_message_id: int,
    article_text: str = "",
) -> dict[str, Any]:
    return {
        "link": link,
        "title": title,
        "summary": summary,
        "feed_published_at": feed_published_at,
        "raw_preview_summary": raw_preview_summary,
        "feed_text_extended": feed_text_extended,
        "article_text": article_text,
        "stage": RAW_REVIEW_STAGE,
        "raw_admin_text": raw_admin_text,
        "admin_message_id": admin_message_id,
        "rewrites": 0,
    }


def resolve_raw_review_preview(link: str, summary: str) -> tuple[str, str]:
    try:
        article_text = fetch_article_text(link)
    except Exception as exc:
        logger.warning("Article fetch failed for raw review preview: %s (%s)", link, exc)
    else:
        first_paragraph = extract_first_paragraph(article_text)
        if first_paragraph:
            logger.info("Raw review preview source: first article paragraph")
            return first_paragraph, article_text
        logger.info("Raw review preview fallback: article extraction returned no paragraphs")

    logger.info("Raw review preview fallback: RSS preview")
    return build_feed_preview_summary(summary), ""


def final_review_summary(pending: dict[str, Any]) -> str:
    return str(pending.get("raw_preview_summary") or pending["summary"])


def parse_callback_data(data: str) -> tuple[str, str]:
    action, draft_id = (data.split(":", 1) + [""])[:2]
    return action, draft_id


async def delete_message_safely(message: Any) -> None:
    if not message:
        return
    with contextlib.suppress(Exception):
        await message.delete()


async def clear_message_keyboard_safely(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    message_id: int,
) -> None:
    with contextlib.suppress(Exception):
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )


async def send_final_review_message(
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    draft_id: str,
    pending: dict[str, Any],
    translated_text: str,
    llm_text_source: str,
) -> int:
    admin_message = await context.bot.send_message(
        chat_id=settings.telegram_admin_chat_id,
        text=build_final_admin_text(
            title=pending["title"],
            summary=final_review_summary(pending),
            translated_text=translated_text,
            source_url=pending["link"],
            llm_text_source=llm_text_source,
        ),
        reply_markup=final_review_keyboard(draft_id),
        disable_web_page_preview=True,
    )
    return admin_message.message_id


def resolve_text_for_llm(
    pending: dict[str, Any],
    draft_id: str,
    store: StateStore,
) -> tuple[str, str]:
    raw_preview_text = str(pending.get("raw_preview_summary", "")).strip()
    if raw_preview_text:
        logger.info("Using raw review preview text for draft=%s", draft_id)
        return raw_preview_text, "первый абзац статьи"

    cached_article_text = str(pending.get("article_text", "")).strip()
    if cached_article_text:
        first_paragraph = extract_first_paragraph(cached_article_text)
        if first_paragraph:
            logger.info("Using first paragraph from cached article text for draft=%s", draft_id)
            return first_paragraph, "первый абзац статьи (cache)"

    source_url = str(pending.get("link", "")).strip()
    if source_url:
        try:
            article_text = fetch_article_text(source_url)
            if article_text:
                pending["article_text"] = article_text
                store.save_pending(draft_id, pending)
                first_paragraph = extract_first_paragraph(article_text)
                if first_paragraph:
                    logger.info(
                        "Using first paragraph from fetched article text for draft=%s chars=%d",
                        draft_id,
                        len(first_paragraph),
                    )
                    return first_paragraph, "первый абзац статьи"
                logger.info(
                    "Fetched article has no paragraphs for draft=%s chars=%d",
                    draft_id,
                    len(article_text),
                )
            logger.info("Full article extraction returned empty for draft=%s", draft_id)
        except Exception as exc:
            logger.warning("Full article fetch failed for draft=%s (%s)", draft_id, exc)

    feed_text_extended = str(pending.get("feed_text_extended", "")).strip()
    summary_text = str(pending.get("summary", "")).strip()
    if len(feed_text_extended) > len(summary_text) + 200:
        logger.info("Using extended RSS content for draft=%s chars=%d", draft_id, len(feed_text_extended))
        return feed_text_extended, "расширенный текст RSS"

    logger.info("Using RSS summary text for draft=%s", draft_id)
    return summary_text, "краткий текст RSS"


async def ensure_admin(update: Update, settings: Settings) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    admin_chat_id = settings.telegram_admin_chat_id
    is_allowed = bool(
        (chat and chat.id == admin_chat_id)
        or (user and user.id == admin_chat_id)
    )
    if not is_allowed:
        logger.info(
            "Admin check failed: expected admin_chat_id=%s, got chat_id=%s, user_id=%s",
            admin_chat_id,
            chat.id if chat else None,
            user.id if user else None,
        )
    return is_allowed


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not await ensure_admin(update, settings):
        return
    await update.message.reply_text(
        "Бот запущен. Команды:\n/check - проверить RSS сейчас\n/stats - статистика\n/clean - очистить pending"
    )


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not await ensure_admin(update, settings):
        return

    store: StateStore = context.application.bot_data["store"]
    channel_blocked = bool(context.application.bot_data.get("channel_blocked"))
    admin_blocked = bool(context.application.bot_data.get("admin_chat_blocked"))
    publish_channel_id, publish_channel_var = resolve_publish_channel(settings)
    publish_channel_name = "unknown"
    try:
        chat = await context.bot.get_chat(publish_channel_id)
        if chat.title:
            publish_channel_name = chat.title
        elif chat.username:
            publish_channel_name = f"@{chat.username}"
        else:
            publish_channel_name = str(chat.id)
    except Exception as exc:
        logger.warning("Failed to resolve publish channel title for stats: %s", exc)

    await update.message.reply_text(
        f"seen_links={store.seen_count()}\n"
        f"pending={store.pending_count()}\n"
        f"admin_chat_blocked={admin_blocked}\n"
        f"channel_blocked={channel_blocked}\n"
        f"publish_mode={'test' if settings.telegram_test_mode else 'prod'}\n"
        f"publish_channel_var={publish_channel_var}\n"
        f"publish_channel_id={publish_channel_id}\n"
        f"publish_channel_name={publish_channel_name}"
    )


async def clean_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not await ensure_admin(update, settings):
        return
    store: StateStore = context.application.bot_data["store"]
    removed = store.clear_pending()
    await update.message.reply_text(f"Pending очищен. Удалено карточек: {removed}")


async def process_feeds(app: Application) -> int:
    settings: Settings = app.bot_data["settings"]
    store: StateStore = app.bot_data["store"]
    lock: asyncio.Lock = app.bot_data["process_lock"]

    created = 0
    feed_errors: list[str] = []
    logger.info("Feed processing started. feeds=%d", len(settings.rss_feeds))
    async with lock:
        for feed_url in settings.rss_feeds:
            logger.info("Fetching feed: %s", feed_url)
            parsed = feedparser.parse(feed_url, agent=USER_AGENT)
            status = getattr(parsed, "status", None)
            if isinstance(status, int) and status >= 400:
                error_text = f"{feed_url}: HTTP {status}"
                feed_errors.append(error_text)
                logger.warning("Feed fetch failed: %s", error_text)
                continue
            if getattr(parsed, "bozo", False):
                error_text = f"{feed_url}: {getattr(parsed, 'bozo_exception', 'parse error')}"
                feed_errors.append(error_text)
                logger.warning("Feed parse warning: %s", error_text)
            entries = list(getattr(parsed, "entries", []))
            logger.info("Feed fetched: %s entries=%d", feed_url, len(entries))

            for entry in entries:
                link = getattr(entry, "link", "").strip()
                title = getattr(entry, "title", "").strip()
                summary = extract_preview_text_from_feed(entry)
                feed_text_extended = extract_extended_text_from_feed(entry)
                if not link or not title:
                    continue
                if not has_required_category(entry):
                    logger.info(
                        "Skipping entry without required category '%s': %s categories=%s",
                        REQUIRED_FEED_CATEGORY,
                        link,
                        entry_categories(entry),
                    )
                    continue
                if store.is_seen(link) or store.has_pending_link(link):
                    continue

                draft_id = uuid.uuid4().hex[:10]
                preview_text, article_text = resolve_raw_review_preview(link=link, summary=summary)
                feed_published_at = format_feed_published_at(entry)
                admin_preview_text = build_raw_admin_text(
                    title=title,
                    summary=preview_text,
                    source_url=link,
                    feed_published_at=feed_published_at,
                )
                try:
                    admin_message = await app.bot.send_message(
                        chat_id=settings.telegram_admin_chat_id,
                        text=admin_preview_text,
                        reply_markup=raw_review_keyboard(draft_id),
                        disable_web_page_preview=True,
                    )
                except Forbidden as exc:
                    if ADMIN_BOT_ID_ERROR in str(exc).lower():
                        app.bot_data["admin_chat_blocked"] = True
                        logger.error(
                            "Invalid TELEGRAM_ADMIN_CHAT_ID=%s. "
                            "Use user/group chat id, not bot id.",
                            settings.telegram_admin_chat_id,
                        )
                        return created
                    raise

                store.save_pending(
                    draft_id,
                    build_pending_payload(
                        link=link,
                        title=title,
                        summary=summary,
                        feed_published_at=feed_published_at,
                        raw_preview_summary=preview_text,
                        feed_text_extended=feed_text_extended,
                        raw_admin_text=admin_preview_text,
                        admin_message_id=admin_message.message_id,
                        article_text=article_text,
                    ),
                )
                created += 1
                logger.info("Created pending draft=%s link=%s", draft_id, link)
                if created >= settings.max_items_per_poll:
                    logger.info("Feed processing limit reached: created=%d", created)
                    app.bot_data["last_feed_errors"] = feed_errors
                    return created
    logger.info("Feed processing finished: created=%d", created)
    app.bot_data["last_feed_errors"] = feed_errors
    return created


async def check_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    if not await ensure_admin(update, settings):
        return

    if context.application.bot_data.get("admin_chat_blocked"):
        await update.message.reply_text(
            "Ошибка конфигурации: TELEGRAM_ADMIN_CHAT_ID указывает на бота. "
            "Укажите id пользователя/группы и перезапустите."
        )
        return

    created = await process_feeds(context.application)
    feed_errors = list(context.application.bot_data.get("last_feed_errors") or [])
    if feed_errors:
        errors_text = "\n".join(feed_errors[:3])
        await update.message.reply_text(
            f"Готово. Новых предложений: {created}\n\n"
            f"Проблемы с RSS:\n{errors_text}"
        )
        return
    await update.message.reply_text(f"Готово. Новых предложений: {created}")


async def publish_to_channel(
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    publish_text: str,
) -> bool:
    publish_channel_id, publish_channel_var = resolve_publish_channel(settings)
    try:
        await context.bot.send_message(
            chat_id=publish_channel_id,
            text=publish_text,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        await context.bot.send_message(
            chat_id=settings.telegram_admin_chat_id,
            text=(
                "Публикация не выполнена: чат канала не найден.\n"
                f"Проверьте {publish_channel_var} (обычно -100... или @username) "
                "и что бот добавлен в канал."
            ),
        )
        logger.error("Channel publish failed (BadRequest): %s", exc)
        return False
    except Forbidden as exc:
        await context.bot.send_message(
            chat_id=settings.telegram_admin_chat_id,
            text=(
                "Публикация не выполнена: у бота нет прав в канале.\n"
                "Добавьте бота в канал и выдайте право на публикацию сообщений."
            ),
        )
        logger.error("Channel publish failed (Forbidden): %s", exc)
        return False
    return True


async def on_admin_reply_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]

    if not await ensure_admin(update, settings):
        return

    message = update.effective_message
    if not message or not message.text or not message.reply_to_message:
        return

    pending_pair = store.find_pending_by_admin_message_id(message.reply_to_message.message_id)
    if not pending_pair:
        return

    draft_id, pending = pending_pair
    stage = str(pending.get("stage", FINAL_REVIEW_STAGE))
    if stage != FINAL_REVIEW_STAGE:
        await message.reply_text("Эта новость еще не на этапе публикации.")
        return

    publish_text = message.text.strip()
    if not publish_text:
        return

    if not await publish_to_channel(context=context, settings=settings, publish_text=publish_text):
        return

    store.mark_seen(pending["link"])
    store.delete_pending(draft_id)
    await clear_message_keyboard_safely(
        context,
        chat_id=settings.telegram_admin_chat_id,
        message_id=int(pending["admin_message_id"]),
    )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    store: StateStore = context.application.bot_data["store"]
    llm: LLMClient = context.application.bot_data["llm"]

    query = update.callback_query
    if not query:
        return
    await query.answer("Обрабатываю...")
    logger.info(
        "Callback received: data=%s chat_id=%s user_id=%s message_id=%s",
        query.data,
        update.effective_chat.id if update.effective_chat else None,
        update.effective_user.id if update.effective_user else None,
        query.message.message_id if query.message else None,
    )

    if not await ensure_admin(update, settings):
        await query.answer("Недостаточно прав для этого действия.", show_alert=True)
        return

    data = query.data or ""
    action, draft_id = parse_callback_data(data)
    logger.info("Callback parsed: action=%s draft_id=%s", action, draft_id)
    pending = store.get_pending(draft_id)
    if not pending:
        logger.info("Callback ignored: pending not found for draft_id=%s", draft_id)
        await query.answer("Эта карточка уже неактуальна.", show_alert=True)
        await delete_message_safely(query.message)
        return

    stage = str(pending.get("stage", FINAL_REVIEW_STAGE))
    logger.info("Callback stage: action=%s draft_id=%s stage=%s", action, draft_id, stage)

    if action == "apr":
        if stage != RAW_REVIEW_STAGE:
            await query.answer("Эта новость уже обработана.", show_alert=False)
            return

        logger.info("APR started for draft_id=%s link=%s", draft_id, pending.get("link"))
        try:
            text_for_llm, text_source = resolve_text_for_llm(
                pending=pending,
                draft_id=draft_id,
                store=store,
            )
            draft_text = llm.make_short_ru_news(
                title=pending["title"],
                text=text_for_llm,
                source_url=pending["link"],
            )
        except Exception as exc:
            logger.exception("LLM generation failed for %s", pending["link"])
            reason = str(exc).strip()
            if len(reason) > 500:
                reason = reason[:500].rstrip() + "..."
            await context.bot.send_message(
                chat_id=settings.telegram_admin_chat_id,
                text=(
                    "Не удалось подготовить перевод для этой новости.\n"
                    f"Причина: {reason}\n"
                    "Попробуйте нажать «Принять в работу» еще раз."
                ),
            )
            return

        logger.info("APR generated draft for draft_id=%s", draft_id)
        await delete_message_safely(query.message)

        admin_message_id = await send_final_review_message(
            context=context,
            settings=settings,
            draft_id=draft_id,
            pending=pending,
            translated_text=draft_text,
            llm_text_source=text_source,
        )
        pending["draft_text"] = draft_text
        pending["llm_text_source"] = text_source
        pending["admin_message_id"] = admin_message_id
        pending["stage"] = FINAL_REVIEW_STAGE
        store.save_pending(draft_id, pending)
        logger.info("APR completed for draft_id=%s moved to final_review", draft_id)
        return

    if action == "pub":
        if stage != FINAL_REVIEW_STAGE:
            await query.answer("Сначала примите новость в работу.", show_alert=False)
            return
        publish_text = llm.to_channel_text(pending["draft_text"])
        if not await publish_to_channel(context=context, settings=settings, publish_text=publish_text):
            return
        store.mark_seen(pending["link"])
        store.delete_pending(draft_id)
        await query.edit_message_reply_markup(reply_markup=None)
        return

    if action == "rej":
        store.mark_seen(pending["link"])
        store.delete_pending(draft_id)
        await delete_message_safely(query.message)
        return

    if action == "rew":
        if stage != FINAL_REVIEW_STAGE:
            await query.answer("Сначала примите новость в работу.", show_alert=False)
            return
        await delete_message_safely(query.message)

        try:
            text_for_llm, text_source = resolve_text_for_llm(
                pending=pending,
                draft_id=draft_id,
                store=store,
            )
            new_text = llm.make_short_ru_news(
                title=pending["title"],
                text=text_for_llm,
                source_url=pending["link"],
                previous_draft=pending["draft_text"],
            )
        except Exception:
            logger.exception("Rewrite failed for %s", pending["link"])
            admin_message_id = await send_final_review_message(
                context=context,
                settings=settings,
                draft_id=draft_id,
                pending=pending,
                translated_text=pending["draft_text"],
                llm_text_source=str(pending.get("llm_text_source", "")),
            )
            pending["admin_message_id"] = admin_message_id
            store.save_pending(draft_id, pending)
            return

        admin_message_id = await send_final_review_message(
            context=context,
            settings=settings,
            draft_id=draft_id,
            pending=pending,
            translated_text=new_text,
            llm_text_source=text_source,
        )
        pending["draft_text"] = new_text
        pending["llm_text_source"] = text_source
        pending["admin_message_id"] = admin_message_id
        pending["rewrites"] = int(pending.get("rewrites", 0)) + 1
        store.save_pending(draft_id, pending)


async def feed_worker(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    while True:
        if app.bot_data.get("admin_chat_blocked"):
            logger.warning("Feed worker paused: admin chat is blocked")
            await asyncio.sleep(max(1, settings.poll_interval_minutes) * 60)
            continue
        try:
            await process_feeds(app)
        except Exception:
            logger.exception("Feed worker error")
        await asyncio.sleep(max(1, settings.poll_interval_minutes) * 60)


async def on_startup(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    me = await app.bot.get_me()
    if settings.telegram_admin_chat_id == me.id:
        app.bot_data["admin_chat_blocked"] = True
        logger.error(
            "Invalid TELEGRAM_ADMIN_CHAT_ID=%s equals bot id=%s. "
            "Set admin to your personal/group chat id.",
            settings.telegram_admin_chat_id,
            me.id,
        )
    else:
        app.bot_data["admin_chat_blocked"] = False

    app.bot_data["channel_blocked"] = False
    publish_channel_id, publish_channel_var = resolve_publish_channel(settings)
    try:
        await app.bot.get_chat(publish_channel_id)
    except BadRequest as exc:
        app.bot_data["channel_blocked"] = True
        logger.error(
            "Invalid %s=%s (%s). Use channel id like -100... or @channel_username.",
            publish_channel_var,
            publish_channel_id,
            exc,
        )
    except Forbidden as exc:
        app.bot_data["channel_blocked"] = True
        logger.error(
            "Bot has no access to %s=%s (%s). Add bot to the channel as admin.",
            publish_channel_var,
            publish_channel_id,
            exc,
        )

    app.bot_data["worker_task"] = asyncio.create_task(feed_worker(app))


async def on_shutdown(app: Application) -> None:
    task = app.bot_data.get("worker_task")
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def build_app(settings: Settings) -> Application:
    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(on_startup)
        .post_shutdown(on_shutdown)
        .build()
    )

    app.bot_data["settings"] = settings
    app.bot_data["store"] = StateStore(settings.state_file)
    app.bot_data["llm"] = LLMClient(
        provider=settings.llm_provider,
        api_key=settings.openai_api_key,
        model=settings.llm_model,
    )
    app.bot_data["process_lock"] = asyncio.Lock()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("check", check_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("clean", clean_cmd))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_admin_reply_publish))
    app.add_error_handler(error_handler)
    return app


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled telegram error", exc_info=context.error)


def main() -> None:
    settings = load_settings()
    app = build_app(settings)
    asyncio.set_event_loop(asyncio.new_event_loop())
    app.run_polling(
        allowed_updates=[
            "message",
            "edited_message",
            "channel_post",
            "edited_channel_post",
            "callback_query",
            "my_chat_member",
            "chat_member",
        ],
        drop_pending_updates=True,
        close_loop=False,
    )


if __name__ == "__main__":
    main()

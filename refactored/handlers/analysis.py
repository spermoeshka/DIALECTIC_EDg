"""
Обработчики команды /daily и /analyze.
"""

import logging
from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message

from config import CACHE_TTL_HOURS
from storage import Storage
from database import upsert_user, increment_requests, get_user

logger = logging.getLogger(__name__)

FREE_DAILY_LIMIT = 5
storage = Storage()


async def check_limit(user_id: int) -> bool:
    user = await get_user(user_id)
    if not user:
        return True
    if user.get("tier") == "pro":
        return True
    return user.get("requests_today", 0) < FREE_DAILY_LIMIT


async def cmd_daily(
    message: Message,
    bot: Bot,
    run_analysis_fn,
):
    user_id = message.from_user.id
    await upsert_user(user_id, message.from_user.username or "")

    if not await check_limit(user_id):
        await message.answer(
            f"⛔ *Лимит* — {FREE_DAILY_LIMIT} запросов/день (free)\n"
            "Попробуй завтра или /subscribe для авторассылки.",
            parse_mode="Markdown"
        )
        return

    text_parts = (message.text or "").split(maxsplit=1)
    force_fresh = (
        len(text_parts) > 1
        and text_parts[1].strip().lower() in ("force", "fresh", "новый", "new")
    )

    from ..services import send_daily_digest_bundle

    cached = None if force_fresh else storage.get_cached_report()
    if cached:
        report = cached["report"]
        prices = cached.get("prices") or {}
        await send_daily_digest_bundle(bot, message.chat.id, user_id, report, prices)
        await message.answer(
            f"Кэш от {cached['timestamp']}. Повтор без AI до ~{CACHE_TTL_HOURS} ч. "
            f"Сброс: `/daily force`",
            parse_mode="Markdown",
        )
        return

    wait_msg = await message.answer(
        "⏳ *Запускаю анализ...*\n\n"
        "🔄 Живые цены → новости → геополитика → дебаты агентов\n"
        "_Займёт 2–5 минут..._",
        parse_mode="Markdown"
    )

    try:
        await increment_requests(user_id)
        report, prices = await run_analysis_fn(user_id)
        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)
        await send_daily_digest_bundle(bot, message.chat.id, user_id, report, prices)

    except Exception as e:
        logger.error(f"Daily error: {e}", exc_info=True)
        await bot.edit_message_text(
            f"❌ *Ошибка:* `{str(e)[:200]}`\n\n"
            "Проверь: API ключи, интернет, BOT_TOKEN.",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="Markdown"
        )


async def cmd_analyze(
    message: Message,
    bot: Bot,
    run_analysis_fn,
):
    user_id = message.from_user.id
    await upsert_user(user_id, message.from_user.username or "")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "❗ *Укажи новость для анализа*\n\n"
            "Примеры:\n"
            "`/analyze Fed снизил ставку до 4.25%`\n"
            "`/analyze Binance заморозила вывод в США`\n"
            "`/analyze Китай ограничил экспорт редкоземельных металлов`",
            parse_mode="Markdown"
        )
        return

    if not await check_limit(user_id):
        await message.answer(
            f"⛔ *Лимит* — {FREE_DAILY_LIMIT} запросов/день (free)",
            parse_mode="Markdown"
        )
        return

    user_news = parts[1].strip()
    wait_msg = await message.answer(
        f"🔍 *Анализирую:*\n_{user_news[:150]}_\n\n"
        "⏳ Ищу контекст + запускаю дебаты...",
        parse_mode="Markdown"
    )

    from ..services import send_daily_digest_bundle

    try:
        await increment_requests(user_id)
        report, prices = await run_analysis_fn(user_id, custom_news=user_news, custom_mode=True)
        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)
        await send_daily_digest_bundle(bot, message.chat.id, user_id, report, prices)

    except Exception as e:
        logger.error(f"Analyze error: {e}", exc_info=True)
        await bot.edit_message_text(
            f"❌ *Ошибка:* `{str(e)[:200]}`",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="Markdown"
        )

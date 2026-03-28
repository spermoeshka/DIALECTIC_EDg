"""
Обработчики для /russia.
"""

import logging
import time
from datetime import datetime
from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from database import upsert_user, increment_requests
from russia_data import fetch_russia_context
from russia_agents import run_russia_analysis
from report_sanitizer import sanitize_full_report
from storage import Storage

from ..utils import split_message, clean_markdown
from ..keyboards import russia_choice_keyboard, feedback_keyboard
from ..services import send_russia_chart_photo

logger = logging.getLogger(__name__)

storage = Storage()


async def cmd_russia(message: Message, bot: Bot, run_russia_analysis_fn):
    user_id = message.from_user.id
    await upsert_user(user_id, message.from_user.username or "")

    FREE_DAILY_LIMIT = 5
    from database import get_user
    user = await get_user(user_id)
    if user and user.get("tier") != "pro" and user.get("requests_today", 0) >= FREE_DAILY_LIMIT:
        await message.answer(
            f"⛔ *Лимит* — {FREE_DAILY_LIMIT} запросов/день (free)",
            parse_mode="Markdown"
        )
        return

    now_ts = time.time()
    from ..state import russia_cache as cache
    if cache.get("report") and (now_ts - cache.get("ts", 0)) < 7200:
        cached_ru = cache["report"]
        await send_russia_chart_photo(bot, message.chat.id, cached_ru)
        for chunk in split_message(cached_ru):
            await message.answer(chunk, parse_mode="Markdown")
        await message.answer(
            f"📦 _Кэш от {cache['timestamp']}. Новый через 2ч._",
            parse_mode="Markdown",
            reply_markup=feedback_keyboard("russia")
        )
        return

    cached = storage.get_cached_report()
    global_report = ""
    if cached and isinstance(cached.get("report"), str):
        global_report = cached["report"]
    if not global_report.strip():
        ur = storage.get_user_last_cached_report(user_id)
        if isinstance(ur, str) and ur.strip():
            global_report = ur

    if not global_report.strip():
        await message.answer(
            "💡 *Совет перед запуском /russia:*\n\n"
            "Глобальный дайджест (/daily) даёт агентам полный контекст рынков.\n"
            "Без него анализ будет работать только на РФ данных.\n\n"
            "*Что делаем?*",
            parse_mode="Markdown",
            reply_markup=russia_choice_keyboard()
        )
        return

    wait_msg = await message.answer(
        "🇷🇺 *Запускаю анализ для России...*\n\n"
        "🔄 ЦБ РФ → Мосбиржа → РБК → Llama агенты → Mistral синтез\n"
        "_Займёт 1–3 минуты..._",
        parse_mode="Markdown"
    )

    try:
        await increment_requests(user_id)
        russia_context = await fetch_russia_context()
        report = await run_russia_analysis_fn(global_report, russia_context)
        report, _san_lines_ru = sanitize_full_report(report)
        if _san_lines_ru:
            logger.info("Russia пост-фильтр: удалено строк: %d", _san_lines_ru)

        from ..state import russia_cache as cache
        cache["report"] = report
        cache["timestamp"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        cache["ts"] = time.time()

        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)

        await send_russia_chart_photo(bot, message.chat.id, report)
        for chunk in split_message(report):
            await message.answer(chunk, parse_mode="Markdown")

        await message.answer(
            "💬 *Был ли анализ полезным?*",
            parse_mode="Markdown",
            reply_markup=feedback_keyboard("russia")
        )

    except Exception as e:
        logger.error(f"Russia error: {e}", exc_info=True)
        await bot.edit_message_text(
            f"❌ *Ошибка:* `{str(e)[:200]}`",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="Markdown"
        )


async def handle_russia_choice(
    callback: CallbackQuery,
    bot: Bot,
    run_russia_analysis_fn,
):
    action = callback.data.split(":")[1]
    user_id = callback.from_user.id

    await callback.message.edit_reply_markup(reply_markup=None)

    if action == "daily":
        await callback.answer()
        await callback.message.answer(
            "✅ Отличный выбор! Запускай /daily — после него /russia выдаст максимум.",
            parse_mode="Markdown"
        )
        return

    await callback.answer("🚀 Запускаю!")

    wait_msg = await callback.message.answer(
        "🇷🇺 *Запускаю анализ для России...*\n\n"
        "🔄 ЦБ РФ → Мосбиржа → РБК → Llama агенты → Mistral синтез\n"
        "_Займёт 1–3 минуты..._",
        parse_mode="Markdown"
    )

    try:
        await increment_requests(user_id)
        global_report = "Глобальный анализ не запускался. Работаю только на данных РФ."
        russia_context = await fetch_russia_context()
        report = await run_russia_analysis_fn(global_report, russia_context)

        from ..state import russia_cache as cache
        cache["report"] = report
        cache["timestamp"] = datetime.now().strftime("%d.%m.%Y %H:%M")
        cache["ts"] = time.time()

        await bot.delete_message(
            chat_id=callback.message.chat.id,
            message_id=wait_msg.message_id
        )

        await send_russia_chart_photo(bot, callback.message.chat.id, report)
        for chunk in split_message(report):
            await callback.message.answer(chunk, parse_mode="Markdown")

        await callback.message.answer(
            "💬 *Был ли анализ полезным?*",
            parse_mode="Markdown",
            reply_markup=feedback_keyboard("russia")
        )

    except Exception as e:
        logger.error(f"Russia choice error: {e}", exc_info=True)
        await bot.edit_message_text(
            f"❌ *Ошибка:* `{str(e)[:200]}`",
            chat_id=callback.message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="Markdown"
        )

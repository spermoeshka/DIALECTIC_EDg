"""
Callback query handlers: дебаты и фидбек.
"""

import logging
from aiogram import Bot
from aiogram.filters import F
from aiogram.types import CallbackQuery

from database import get_debate_session
from debate_storage import get_debate_redis
from storage import Storage

from ..utils import debate_plain_text
from ..keyboards import debates_keyboard, feedback_keyboard
from ..state import debate_cache

logger = logging.getLogger(__name__)
storage = Storage()


async def handle_debate_page(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer()
        return

    _, user_id_str, action = parts[0], parts[1], parts[2]

    if action == "noop":
        await callback.answer()
        return

    try:
        kb_uid = int(user_id_str)
    except ValueError:
        await callback.answer()
        return

    if kb_uid != callback.from_user.id:
        await callback.answer("Кнопка не с твоего аккаунта", show_alert=True)
        return

    user_id = callback.from_user.id
    try:
        round_idx = int(action)
    except ValueError:
        await callback.answer()
        return

    from ..utils import hydrate_debate_from_report

    cache = debate_cache.get(user_id)
    if not cache:
        report_redis = await get_debate_redis(user_id)
        cache = hydrate_debate_from_report(report_redis) if report_redis else None
        if cache:
            debate_cache[user_id] = cache
    if not cache:
        report_db = await get_debate_session(user_id)
        cache = hydrate_debate_from_report(report_db) if report_db else None
        if cache:
            debate_cache[user_id] = cache
    if not cache:
        storage.reload_from_disk()
        snap = storage.get_user_debate_snapshot(user_id)
        cache = hydrate_debate_from_report(snap) if snap else None
        if cache:
            debate_cache[user_id] = cache
    if not cache:
        storage.reload_from_disk()
        rep_user = storage.get_user_last_cached_report(user_id)
        cache = hydrate_debate_from_report(rep_user) if rep_user else None
        if cache:
            debate_cache[user_id] = cache
    if not cache:
        storage.reload_from_disk()
        cached = storage.get_cached_report()
        rep = cached.get("report") if cached else None
        cache = hydrate_debate_from_report(rep) if rep else None
        if cache:
            debate_cache[user_id] = cache

    if not cache:
        logger.warning(
            "debate hydrate miss user_id=%s — кэш пуст (редеплой/другой воркер). "
            "Файл .txt с дебатами уже в чате под дайджестом.",
            user_id,
        )
        await callback.answer(
            "Дебаты в файле dialectic_debates_….txt — пролистай чат ниже кнопок. "
            "Кнопки листания работают только пока бот не перезапускали.",
            show_alert=True,
        )
        return

    rounds = cache["rounds"]
    if round_idx >= len(rounds):
        await callback.answer()
        return

    round_text = debate_plain_text(rounds[round_idx])

    if len(round_text) > 4080:
        round_text = round_text[:4050] + "\n\n(…сокращено)"

    kb = debates_keyboard(user_id, round_idx, len(rounds))

    try:
        await callback.message.edit_text(round_text, reply_markup=kb)
    except Exception as e:
        logger.warning("debate edit_text: %s", e)
        try:
            await callback.message.answer(round_text, reply_markup=kb)
        except Exception as e2:
            logger.error("debate answer fallback: %s", e2)

    await callback.answer()


async def handle_feedback(callback: CallbackQuery, bot: Bot):
    from database import save_feedback
    
    _, rating_str, report_type = callback.data.split(":")
    await save_feedback(callback.from_user.id, report_type, int(rating_str))
    emoji = "🙏 Спасибо!" if int(rating_str) == 1 else "📝 Учтём!"
    await callback.answer(emoji)
    await callback.message.edit_reply_markup(reply_markup=None)

"""
Обработчики базовых команд (/start, /help, /stats, /admin).
"""

from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message

from config import ADMIN_IDS
from database import upsert_user, get_user, get_feedback_stats, get_track_record, get_admin_stats
from user_profile import get_profile, RISK_PROFILES, HORIZONS

FREE_DAILY_LIMIT = 5


async def cmd_start(message: Message, bot: Bot):
    await upsert_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or ""
    )
    name = message.from_user.first_name or "трейдер"
    await message.answer(
        f"👋 Привет, *{name}*!\n\n"
        "🧠 *Dialectic Edge* — честный AI-аналитик рынков\n\n"
        "4 агента спорят используя *живые данные*:\n"
        "🐂 *Bull* — ищет возможности роста\n"
        "🐻 *Bear* — указывает риски\n"
        "🔍 *Verifier* — проверяет каждую цифру\n"
        "⚖️ *Synth* — итог адаптированный под тебя\n\n"
        "📋 *Команды:*\n"
        "• /profile — настрой риск-профиль (важно сделать первым)\n"
        "• /daily — дайджест рынков\n"
        "• /analyze [текст] — анализ новости\n"
        "• /trackrecord — история точности агентов\n"
        "• /weeklyreport — отчёт за неделю\n"
        "• /subscribe — авторассылка\n"
        "• /markets — текущие цены\n"
        "• /russia — анализ для российского рынка 🇷🇺\n\n"
        "⚠️ _Не финансовый совет. Будущее неизвестно никому._",
        parse_mode="Markdown"
    )


async def cmd_help(message: Message, bot: Bot):
    await upsert_user(message.from_user.id)
    await message.answer(
        "📖 *Dialectic Edge v7.1*\n\n"
        "*Что нового в v6:*\n"
        "• Один отчёт вместо 6 сообщений\n"
        "• Кнопка 📖 Полные дебаты — листай раунды\n"
        "• Простой язык в выводах\n"
        "• Умный Risk/Reward — если риск высокий, бот честно скажет 'ВНЕ РЫНКА'\n\n"
        "*Команды:*\n"
        "• `/profile` — настрой риск-профиль первым\n"
        "• `/daily` — дайджест (из кэша до суток без токенов)\n"
        "• `/daily force` — принудительно новый AI-прогон\n"
        "• `/analyze [текст]` — анализ новости\n"
        "• `/markets` — живые цены\n"
        "• `/trackrecord` — история точности\n"
        "• `/weeklyreport` — отчёт за неделю\n"
        "• `/subscribe on 08:00` — авторассылка\n"
        "• `/russia` — анализ для российского рынка 🇷🇺\n"
        "• `/stats` — твоя статистика\n\n"
        "⚠️ _Не финансовый совет. Будущее неизвестно никому._",
        parse_mode="Markdown"
    )


async def cmd_stats(message: Message, bot: Bot):
    user_id = message.from_user.id
    await upsert_user(user_id)
    user = await get_user(user_id)
    profile = await get_profile(user_id)

    if not user:
        await message.answer("Ошибка загрузки.")
        return

    fb = await get_feedback_stats()
    total_fb = fb.get("total") or 0
    pos_fb = fb.get("positive") or 0
    satisfaction = (pos_fb / total_fb * 100) if total_fb > 0 else 0

    risk_name = RISK_PROFILES.get(profile.get("risk", "moderate"), {}).get("name", "⚖️ Умеренный")
    horizon_name = HORIZONS.get(profile.get("horizon", "swing"), {}).get("name", "📈 Свинг")

    tr = await get_track_record()
    tr_s = tr["stats"]
    tr_wins = tr_s.get("wins") or 0
    tr_loss = tr_s.get("losses") or 0
    tr_wr = (tr_wins / (tr_wins + tr_loss) * 100) if (tr_wins + tr_loss) > 0 else 0

    await message.answer(
        f"📈 *Моя статистика*\n\n"
        f"*Tier:* {'👑 PRO' if user.get('tier')=='pro' else '🆓 Free'}\n"
        f"*Запросов сегодня:* {user.get('requests_today',0)}/{FREE_DAILY_LIMIT}\n"
        f"*Запросов всего:* {user.get('requests_total',0)}\n"
        f"*Профиль:* {risk_name} | {horizon_name}\n"
        f"*Подписка:* {'✅' if user.get('daily_sub') else '❌'}\n\n"
        f"*🎯 Track Record бота:*\n"
        f"Прогнозов: {tr_s.get('total',0)} | Winrate: {tr_wr:.0f}%\n\n"
        f"*Оценки пользователей:*\n"
        f"Оценок: {total_fb} | Позитивных: {satisfaction:.0f}%\n\n"
        f"• /trackrecord — полная история точности\n"
        f"• /weeklyreport — отчёт за неделю\n"
        f"• /profile — изменить профиль",
        parse_mode="Markdown"
    )


async def cmd_admin(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    stats = await get_admin_stats()
    fb = await get_feedback_stats()
    tr = await get_track_record()
    tr_stats = tr["stats"]
    wins = tr_stats.get("wins") or 0
    losses = tr_stats.get("losses") or 0
    winrate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    await message.answer(
        f"🔧 *ADMIN*\n\n"
        f"👥 Пользователи: {stats['total_users']} | Активных: {stats['active_week']}\n"
        f"📬 Подписчики: {stats['subscribers']}\n"
        f"📊 Запросов: {stats['total_reports']}\n\n"
        f"👍 Фидбек: {fb.get('positive',0)}+ / {fb.get('negative',0)}-\n\n"
        f"🎯 Track Record:\n"
        f"Прогнозов: {tr_stats.get('total',0)} | Winrate: {winrate:.0f}%\n"
        f"Avg P&L: {(tr_stats.get('avg_pnl') or 0):+.1f}%",
        parse_mode="Markdown"
    )

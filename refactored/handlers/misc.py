"""
Miscellaneous handlers: /markets, /trackrecord, /weeklyreport, /subscribe.
"""

import logging
from datetime import datetime
from aiogram import Bot
from aiogram.filters import Command
from aiogram.types import Message

from database import upsert_user, get_track_record
from weekly_report import build_weekly_report
from web_search import get_full_realtime_context
from user_profile import get_profile

from ..utils import clean_markdown
from ..services import send_daily_digest_bundle

logger = logging.getLogger(__name__)


async def cmd_markets(message: Message, bot: Bot):
    await upsert_user(message.from_user.id)
    wait_msg = await message.answer("⏳ Загружаю живые данные...")
    try:
        _, live_prices = await get_full_realtime_context()
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        safe_prices = clean_markdown(live_prices)
        await bot.edit_message_text(
            f"📊 *РЫНКИ — {now}*\n\n{safe_prices}",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id,
            parse_mode="Markdown"
        )
    except Exception as e:
        await bot.edit_message_text(
            f"❌ Ошибка: {e}",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id
        )


async def cmd_trackrecord(message: Message, bot: Bot):
    await upsert_user(message.from_user.id)
    try:
        data = await get_track_record()
        stats = data["stats"]
        recent = data["recent"]
        by_asset = data["by_asset"]

        total = stats.get("total") or 0
        wins = stats.get("wins") or 0
        losses = stats.get("losses") or 0
        pending = stats.get("pending") or 0
        avg_pnl = stats.get("avg_pnl") or 0
        best = stats.get("best_call") or 0
        worst = stats.get("worst_call") or 0

        if total == 0:
            await message.answer(
                "📊 *Track Record*\n\n"
                "_Прогнозы накапливаются. Запусти /daily — агенты начнут делать прогнозы._\n\n"
                "Через 1-2 недели активного использования здесь появится реальная статистика.",
                parse_mode="Markdown"
            )
            return

        finished = wins + losses
        winrate = (wins / finished * 100) if finished > 0 else 0
        wr_emoji = "🟢" if winrate >= 55 else "🟡" if winrate >= 45 else "🔴"
        pnl_emoji = "🟢" if avg_pnl >= 0 else "🔴"

        lines = [
            "📊 *TRACK RECORD АГЕНТОВ*\n",
            f"*Всего прогнозов:* {total}",
            f"*Завершено:* {finished} | ⏳ Ждут: {pending}",
        ]

        if finished > 0:
            lines += [
                f"*Winrate:* {wr_emoji} *{winrate:.0f}%* ({wins}✅ / {losses}❌)",
                f"*Средний P&L:* {pnl_emoji} *{avg_pnl:+.1f}%*",
            ]
            if best:
                lines.append(f"*Лучший:* 🚀 +{best:.1f}%")
            if worst and worst < 0:
                lines.append(f"*Худший:* 💥 {worst:.1f}%")

        if by_asset:
            lines.append("\n*🏆 Топ активов:*")
            for a in by_asset[:3]:
                wr = (a['wins'] / a['calls'] * 100) if a['calls'] else 0
                lines.append(
                    f"• {a['asset']}: {wr:.0f}% winrate "
                    f"({a['calls']} сигналов, avg {a['avg_pnl']:+.1f}%)"
                )

        if recent:
            lines.append("\n*📋 Последние сигналы:*")
            for r in recent[:5]:
                emoji = "✅" if r["result"] == "win" else "❌"
                pnl = r.get("pnl_pct") or 0
                lines.append(
                    f"{emoji} {r['asset']} {r['direction']} "
                    f"→ *{pnl:+.1f}%* _{(r.get('created_at') or '')[:10]}_"
                )

        lines.append(
            "\n⚠️ _Прошлые результаты не гарантируют будущих. Не финансовый совет._"
        )
        await message.answer("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.error(f"Trackrecord error: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка: {e}")


async def cmd_weekly(message: Message, bot: Bot):
    await upsert_user(message.from_user.id)
    wait_msg = await message.answer("⏳ Формирую отчёт за неделю...")
    try:
        report = await build_weekly_report()
        await bot.delete_message(chat_id=message.chat.id, message_id=wait_msg.message_id)
        await message.answer(report, parse_mode="Markdown")
    except Exception as e:
        await bot.edit_message_text(
            f"❌ Ошибка: {e}",
            chat_id=message.chat.id,
            message_id=wait_msg.message_id
        )


async def cmd_subscribe(message: Message, bot: Bot):
    from database import get_user, set_daily_sub
    
    user_id = message.from_user.id
    await upsert_user(user_id)
    user = await get_user(user_id)
    is_subbed = user.get("daily_sub", 0) if user else 0
    sub_time = user.get("sub_time", "08:00") if user else "08:00"
    parts = message.text.split()

    if len(parts) == 1:
        status = f"✅ Активна (каждый день в *{sub_time} UTC*)" if is_subbed else "❌ Отключена"
        await message.answer(
            f"📬 *Авторассылка*\nСтатус: {status}\n\n"
            f"• `/subscribe on` — включить в 08:00 UTC\n"
            f"• `/subscribe on 09:30` — своё время\n"
            f"• `/subscribe off` — отключить",
            parse_mode="Markdown"
        )
        return

    action = parts[1].lower()
    time_str = parts[2] if len(parts) > 2 else "08:00"
    try:
        h, m = time_str.split(":")
        assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        time_str = f"{int(h):02d}:{int(m):02d}"
    except Exception:
        await message.answer("❌ Формат: HH:MM, например `08:30`", parse_mode="Markdown")
        return

    if action == "on":
        await set_daily_sub(user_id, True, time_str)
        await message.answer(
            f"✅ *Подписка активна*\nКаждый день в *{time_str} UTC*\n\n"
            f"Отключить: `/subscribe off`",
            parse_mode="Markdown"
        )
    elif action == "off":
        await set_daily_sub(user_id, False)
        await message.answer("❌ *Подписка отключена*", parse_mode="Markdown")

"""
Dialectic Edge v7.1 — Рефакторенная версия.

Структура:
- handlers/ — обработчики команд (разбиты по функционалу)
- keyboards.py — клавиатуры
- services.py — бизнес-логика
- utils.py — утилиты
- state.py — глобальное состояние
"""

import asyncio
import logging
import os
from functools import partial

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from config import (
    BOT_TOKEN, ADMIN_IDS, REDIS_URL, DEBATE_SNAPSHOT_HOURS,
    USING_DATA_DIR, DB_PATH, CACHE_FILE,
)
from database import init_db, import_forecasts_from_markdown
from debate_storage import ping_redis
from scheduler import Scheduler
from user_profile import init_profiles_table
from tracker import check_pending_predictions
from russia_agents import run_russia_analysis

from .services import run_full_analysis, set_scheduler, deliver_scheduled_daily
from .handlers import (
    cmd_start, cmd_help, cmd_stats, cmd_admin,
    cmd_profile, handle_profile,
    cmd_daily, cmd_analyze,
    cmd_russia, handle_russia_choice,
    cmd_markets, cmd_trackrecord, cmd_weekly, cmd_subscribe,
    handle_debate_page, handle_feedback,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def setup_handlers():
    dp.message(Command("start")(cmd_start))
    dp.message(Command("help")(cmd_help))
    dp.message(Command("stats")(cmd_stats))
    dp.message(Command("admin")(cmd_admin))

    dp.message(Command("profile")(cmd_profile))
    dp.callback_query(F.data.startswith("profile:"))(handle_profile)

    dp.message(Command("daily")(partial(cmd_daily, run_analysis_fn=run_full_analysis)))
    dp.message(Command("analyze")(partial(cmd_analyze, run_analysis_fn=run_full_analysis)))

    dp.message(Command("russia")(partial(cmd_russia, run_russia_analysis_fn=run_russia_analysis))
    )
    dp.callback_query(F.data.startswith("russia_choice:"))(
        partial(handle_russia_choice, run_russia_analysis_fn=run_russia_analysis)
    )

    dp.message(Command("markets")(cmd_markets))
    dp.message(Command("trackrecord")(cmd_trackrecord))
    dp.message(Command("weeklyreport")(cmd_weekly))
    dp.message(Command("subscribe")(cmd_subscribe))

    dp.callback_query(F.data.startswith("debate:"))(handle_debate_page)
    dp.callback_query(F.data.startswith("fb:"))(handle_feedback)


async def main():
    await init_db()
    await import_forecasts_from_markdown()
    await init_profiles_table()
    logger.info("🚀 Dialectic Edge v7.1 (refactored) starting...")

    if int(os.getenv("RAILWAY_REPLICA_COUNT", "1") or "1") > 1:
        logger.warning(
            "Railway: у сервиса бота >1 реплики — aiogram polling даёт TelegramConflictError. "
            "Scale → 1 или один процесс с BOT_TOKEN."
        )

    if USING_DATA_DIR:
        logger.info("Постоянное хранилище: SQLite=%s | cache.json=%s", DB_PATH, CACHE_FILE)

    if REDIS_URL.strip():
        if await ping_redis():
            logger.info(
                "Redis OK — полные дебаты переживут рестарт (TTL ≈ %s ч.)",
                DEBATE_SNAPSHOT_HOURS,
            )
        else:
            logger.warning("REDIS_URL задан, но соединение не удалось")
    else:
        logger.warning(
            "REDIS_URL нет — после редеплоя кнопка «Полные дебаты» может быть пустой."
        )

    scheduler = Scheduler(
        bot=bot,
        send_daily_fn=partial(deliver_scheduled_daily, run_analysis_fn=run_full_analysis),
        check_predictions_fn=check_pending_predictions
    )
    set_scheduler(scheduler)

    setup_handlers()

    await asyncio.gather(
        dp.start_polling(bot),
        scheduler.start()
    )


if __name__ == "__main__":
    asyncio.run(main())

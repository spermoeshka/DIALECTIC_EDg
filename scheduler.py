"""
scheduler.py — Фоновые задачи по расписанию.
- Ежедневная рассылка дайджеста подписчикам
- Проверка прогнозов каждые 6 часов
- Сброс счётчиков запросов в полночь
- Экспорт track record на GitHub — сразу при старте + после каждого /daily
"""
import asyncio
import logging
from datetime import datetime
from database import (
    get_daily_subscribers,
    reset_daily_counts,
    get_admin_stats
)
logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, bot, send_daily_fn, check_predictions_fn):
        self.bot = bot
        self.send_daily = send_daily_fn
        self.check_predictions = check_predictions_fn
        self._running = False

    async def start(self):
        """Запускает все фоновые задачи."""
        self._running = True
        logger.info("⏰ Scheduler запущен")

        await asyncio.gather(
            self._daily_digest_loop(),
            self._prediction_checker_loop(),
            self._midnight_reset_loop(),
            self._biweekly_github_export_loop(),
        )

    async def _daily_digest_loop(self):
        """Каждую минуту проверяет — не пора ли слать дайджест."""
        while self._running:
            try:
                now = datetime.now()
                current_time = now.strftime("%H:%M")
                subscribers = await get_daily_subscribers()
                for user in subscribers:
                    sub_time = user.get("sub_time", "08:00")
                    if sub_time == current_time:
                        logger.info(f"📬 Отправляю дайджест пользователю {user['user_id']}")
                        try:
                            await self.send_daily(user["user_id"])
                        except Exception as e:
                            logger.warning(f"Ошибка рассылки для {user['user_id']}: {e}")
            except Exception as e:
                logger.error(f"Daily digest loop error: {e}")
            await asyncio.sleep(60)

    async def _prediction_checker_loop(self):
        """Проверяет прогнозы каждые 6 часов."""
        while self._running:
            try:
                logger.info("🔍 Проверяю прогнозы агентов...")
                checked = await self.check_predictions()
                logger.info(f"Проверено прогнозов: {checked}")
            except Exception as e:
                logger.error(f"Prediction checker error: {e}")
            await asyncio.sleep(6 * 3600)

    async def _midnight_reset_loop(self):
        """Сбрасывает счётчики запросов в полночь."""
        while self._running:
            now = datetime.now()
            seconds_to_midnight = (
                (24 - now.hour - 1) * 3600
                + (60 - now.minute - 1) * 60
                + (60 - now.second)
            )
            await asyncio.sleep(seconds_to_midnight)
            try:
                await reset_daily_counts()
                logger.info("🌙 Счётчики запросов сброшены (полночь)")
            except Exception as e:
                logger.error(f"Midnight reset error: {e}")

    async def _biweekly_github_export_loop(self):
        """
        Экспортирует track record на GitHub.
        Первый раз — СРАЗУ при старте бота.
        Потом — раз в 2 недели.
        """
        while self._running:
            try:
                from github_export import export_to_github
                success = await export_to_github()
                if success:
                    logger.info("✅ Track record экспортирован на GitHub")
                else:
                    logger.warning("⚠️ GitHub export не выполнен — проверь GITHUB_TOKEN в Railway")
            except Exception as e:
                logger.error(f"GitHub export error: {e}")

            await asyncio.sleep(14 * 24 * 3600)  # раз в 2 недели

    async def export_now(self):
        """
        Принудительный экспорт — вызывается из main.py после каждого /daily.
        Так FORECASTS.md обновляется каждый день, не раз в 2 недели.
        """
        try:
            from github_export import export_to_github
            await export_to_github()
            logger.info("✅ GitHub export (после /daily) выполнен")
        except Exception as e:
            logger.warning(f"GitHub export (manual) error: {e}")

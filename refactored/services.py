"""
Бизнес-логика анализа и рассылки.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile

from config import CACHE_TTL_HOURS
from news_fetcher import NewsFetcher
from data_sources import fetch_full_context
from web_search import get_full_realtime_context, search_news_context
from meta_analyst import get_meta_context
from sentiment import analyze_and_filter_async, format_for_agents
from agents import DebateOrchestrator
from report_sanitizer import sanitize_full_report
from chart_generator import generate_main_chart, generate_russia_chart
from storage import Storage
from database import (
    log_report, get_track_record, save_predictions_from_report,
    get_previous_digest,
)
from tracker import check_pending_predictions
from scheduler import Scheduler
from user_profile import build_profile_instruction
from github_export import push_digest_cache
from debate_storage import save_debate_redis

from .utils import (
    clean_markdown, debate_plain_text, split_message,
    extract_signal_pct_and_stars, signal_to_stars,
    parse_report_parts, hydrate_debate_from_report,
    build_short_report, SIGNAL_PCT_EXPLAINED,
)
from .keyboards import main_report_keyboard, feedback_keyboard

logger = logging.getLogger(__name__)

fetcher = NewsFetcher()
storage = Storage()
scheduler: Optional[Scheduler] = None


async def check_limit(user_id: int, get_user, FREE_DAILY_LIMIT: int = 5) -> bool:
    user = await get_user(user_id)
    if not user:
        return True
    if user.get("tier") == "pro":
        return True
    return user.get("requests_today", 0) < FREE_DAILY_LIMIT


async def run_full_analysis(
    user_id: int,
    custom_news: str = "",
    custom_mode: bool = False,
) -> tuple[str, dict]:
    tasks = [
        fetcher.fetch_all(),
        fetch_full_context(),
        get_full_realtime_context(),
        get_meta_context(),
        get_previous_digest(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)
    news, geo_context, realtime_result, meta_context, prev_digest = results

    if isinstance(prev_digest, Exception):
        prev_digest = ""

    if isinstance(realtime_result, Exception):
        prices_dict, live_prices = {}, ""
    elif isinstance(realtime_result, tuple) and len(realtime_result) == 2:
        prices_dict, live_prices = realtime_result
    else:
        prices_dict, live_prices = {}, ""

    if isinstance(news, Exception):
        news = ""
    if isinstance(geo_context, Exception):
        geo_context = ""
    if isinstance(live_prices, Exception):
        live_prices = ""
    if isinstance(meta_context, Exception):
        meta_context = ""

    from user_profile import get_profile
    profile = await get_profile(user_id)
    if isinstance(profile, Exception):
        profile = {"risk": "moderate", "horizon": "swing", "markets": "all"}

    profile_instruction = build_profile_instruction(profile)

    if custom_mode and custom_news:
        web_context = await search_news_context(custom_news)
        news_context = (
            f"ТЕМА АНАЛИЗА: {custom_news}\n\n"
            f"{web_context}\n\n{geo_context}\n\n{meta_context}"
        )
    else:
        news_context = (
            f"{geo_context}\n\n=== НОВОСТИ ===\n{news}\n\n{meta_context}"
        )

    if prev_digest and not custom_mode:
        news_context += f"\n\n{prev_digest}"

    sentiment_result, confidence_instruction = await analyze_and_filter_async(
        news_context, str(live_prices)
    )
    sentiment_block = format_for_agents(sentiment_result, confidence_instruction)

    logger.info(
        f"Sentiment: {sentiment_result.label} | "
        f"Confidence: {sentiment_result.confidence} | "
        f"Score: {sentiment_result.score:+.2f}"
    )

    prices_dict = dict(prices_dict) if prices_dict else {}
    prices_dict["SENTIMENT"] = {
        "score": sentiment_result.score,
        "label": sentiment_result.label,
        "confidence": sentiment_result.confidence,
    }

    orchestrator = DebateOrchestrator()
    report = await orchestrator.run_debate(
        news_context=news_context,
        live_prices=live_prices,
        profile_instruction=profile_instruction + sentiment_block,
        custom_mode=custom_mode
    )
    report, _san_lines = sanitize_full_report(report)
    if _san_lines:
        logger.info("Пост-фильтр полного отчёта: удалено строк: %s", _san_lines)

    _conf_raw = sentiment_result.confidence
    _conf_map = {"HIGH": 0.85, "MEDIUM": 0.55, "LOW": 0.25, "EXTREME": 0.95}
    if isinstance(_conf_raw, str):
        _conf_num = _conf_map.get(_conf_raw.upper(), 0.5)
    else:
        try:
            _conf_num = float(_conf_raw)
        except (TypeError, ValueError):
            _conf_num = 0.5

    stars = signal_to_stars(_conf_num)
    pct = int(_conf_num * 100)

    separator = "─" * 30 + "\n"
    signal_line = (
        f"📶 *Уровень сигнала:* {stars} ({pct}% — уверенность FinBERT в тоне новостей)\n"
        f"_Не направление рынка; расшифровка — в шапке дайджеста._\n\n"
    )
    report = report.replace(separator, separator + signal_line, 1)

    source = custom_news[:300] if custom_mode else str(news)[:300]
    await save_predictions_from_report(report, source_news=source)
    await log_report(
        user_id,
        "analyze" if custom_mode else "daily",
        source,
        report[:500]
    )

    if not custom_mode:
        storage.cache_report(report, prices_dict, owner_user_id=user_id)
        if scheduler is not None:
            asyncio.create_task(scheduler.export_now())
        try:
            date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
            asyncio.create_task(push_digest_cache(report, date_str))
        except Exception as e:
            logger.warning(f"Digest cache error: {e}")

    return report, prices_dict


async def send_debates_attachment(
    bot: Bot, chat_id: int, rounds: list[str]
) -> None:
    if not rounds:
        return
    blocks: list[str] = []
    for i, r in enumerate(rounds, 1):
        blocks.append(f"{'═' * 12} Раунд {i} {'═' * 12}\n\n{debate_plain_text(r)}")
    body = "\n\n".join(blocks)
    raw = body.encode("utf-8")
    max_bytes = 48 * 1024 * 1024
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        body = raw.decode("utf-8", errors="ignore") + "\n\n…файл обрезан по лимиту Telegram"
        raw = body.encode("utf-8")
    fn = f"dialectic_debates_{datetime.now().strftime('%Y-%m-%d_%H%M')}.txt"
    try:
        await bot.send_document(
            chat_id,
            document=BufferedInputFile(raw, filename=fn),
            caption="📖 Все раунды дебатов в файле — он не пропадёт при рестарте бота.",
        )
    except Exception as e:
        logger.warning("Не удалось отправить файл дебатов: %s", e)


async def send_digest_chart(
    bot: Bot, chat_id: int, report: str,
    prices_dict: dict, stars_str: str, pct_val: int,
) -> None:
    try:
        buf = generate_main_chart(report, prices_dict or {}, stars_str, pct_val)
        if not buf:
            return
        raw = buf.getvalue() if hasattr(buf, "getvalue") else buf.read()
        await bot.send_photo(
            chat_id,
            photo=BufferedInputFile(raw, filename="dialectic_edge.png"),
        )
    except Exception as e:
        logger.warning("Карточка-график не отправлена: %s", e)


async def send_russia_chart_photo(
    bot: Bot, chat_id: int, report: str
) -> None:
    try:
        buf = generate_russia_chart(report)
        if not buf:
            return
        raw = buf.getvalue() if hasattr(buf, "getvalue") else buf.read()
        await bot.send_photo(
            chat_id,
            photo=BufferedInputFile(raw, filename="russia_edge.png"),
        )
    except Exception as e:
        logger.warning("Карточка /russia не отправлена: %s", e)


async def send_daily_digest_bundle(
    bot: Bot, chat_id: int, user_id: int, report: str, prices_dict: dict,
) -> None:
    from database import save_debate_session
    from .state import debate_cache, russia_cache

    parts = parse_report_parts(report)
    pct_val, stars_str = extract_signal_pct_and_stars(report)
    hid = hydrate_debate_from_report(report)
    if hid:
        debate_cache[user_id] = hid
    else:
        debate_cache[user_id] = {"rounds": parts["rounds"], "full": report}

    try:
        await save_debate_session(user_id, report)
    except Exception as e:
        logger.warning("save_debate_session: %s", e)
    try:
        await save_debate_redis(user_id, report)
    except Exception as e:
        logger.warning("save_debate_redis: %s", e)
    try:
        storage.save_user_debate_snapshot(user_id, report)
    except Exception as e:
        logger.warning("save_user_debate_snapshot: %s", e)

    messages = build_short_report(parts, stars_str, pct_val)
    logger.info(f"Отправляю {len(messages)} сообщений")

    for i, msg in enumerate(messages):
        await bot.send_message(chat_id, msg)
        if i == 0:
            await send_digest_chart(bot, chat_id, report, prices_dict or {}, stars_str, pct_val)
        await asyncio.sleep(0.3)

    await bot.send_message(
        chat_id,
        "Полный анализ выше.\n"
        "📎 Сразу после этой кнопки придёт файл со всеми дебатами — он не пропадёт при рестарте бота.",
        reply_markup=main_report_keyboard(
            user_id, has_debates=bool(debate_cache.get(user_id, {}).get("rounds")),
        ),
    )
    rounds_out = debate_cache.get(user_id, {}).get("rounds") or []
    if rounds_out:
        await asyncio.sleep(0.25)
        await send_debates_attachment(bot, chat_id, rounds_out)


async def deliver_scheduled_daily(
    bot: Bot, user_id: int, run_analysis_fn
) -> None:
    try:
        cached = storage.get_cached_report()
        if cached:
            report = cached["report"]
            prices = cached.get("prices") or {}
            await send_daily_digest_bundle(bot, user_id, user_id, report, prices)
            return
        report, prices = await run_analysis_fn(user_id)
        await send_daily_digest_bundle(bot, user_id, user_id, report, prices)
    except Exception as e:
        logger.warning("Рассылка дайджеста user %s: %s", user_id, e)


def set_scheduler(s: Scheduler) -> None:
    global scheduler
    scheduler = s

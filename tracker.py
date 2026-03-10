"""
tracker.py — Автоматическая проверка прогнозов агентов.

Каждые несколько часов проверяет pending-прогнозы,
сравнивает с реальными ценами и сохраняет результат win/loss.
Это строит track record — главный конкурентный ров.
"""

import asyncio
import logging
import re
from datetime import datetime

from market_data import MarketDataFetcher
from database import (
    get_pending_predictions,
    update_prediction_result,
    save_prediction
)

logger = logging.getLogger(__name__)

market = MarketDataFetcher()

# Маппинг названий активов → тикеры для Yahoo/CoinGecko
ASSET_MAP = {
    # Крипта → CoinGecko IDs
    "BTC": ("crypto", "bitcoin"),
    "ETH": ("crypto", "ethereum"),
    "SOL": ("crypto", "solana"),
    "BNB": ("crypto", "binancecoin"),
    # Акции/ETF → Yahoo Finance
    "SPY": ("stock", "SPY"),
    "QQQ": ("stock", "QQQ"),
    "NVDA": ("stock", "NVDA"),
    "AAPL": ("stock", "AAPL"),
    "TSLA": ("stock", "TSLA"),
    "GLD": ("stock", "GLD"),
}


async def get_current_price(asset: str) -> float | None:
    """Получает текущую цену актива."""
    asset_upper = asset.upper()
    
    if asset_upper not in ASSET_MAP:
        return None
    
    asset_type, identifier = ASSET_MAP[asset_upper]
    
    try:
        if asset_type == "crypto":
            import aiohttp
            url = "https://api.coingecko.com/api/v3/simple/price"
            params = {"ids": identifier, "vs_currencies": "usd"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    return data.get(identifier, {}).get("usd")

        elif asset_type == "stock":
            import aiohttp
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{identifier}"
            headers = {"User-Agent": "Mozilla/5.0"}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    meta = data["chart"]["result"][0]["meta"]
                    return meta.get("regularMarketPrice")

    except Exception as e:
        logger.warning(f"Price fetch error for {asset}: {e}")
        return None


async def check_pending_predictions():
    """
    Проверяет все pending-прогнозы.
    Вызывается по расписанию (каждые 6 часов).
    """
    pending = await get_pending_predictions()
    
    if not pending:
        logger.info("Нет pending-прогнозов для проверки")
        return 0

    checked = 0
    for pred in pending:
        current_price = await get_current_price(pred["asset"])
        
        if current_price is None:
            continue
        
        entry = pred["entry_price"]
        target = pred["target_price"]
        stop = pred["stop_loss"]
        direction = pred["direction"].upper()
        
        if not entry or not target or not stop:
            continue
        
        # Определяем результат
        result = "pending"
        pnl_pct = 0.0

        if direction == "LONG":
            if current_price >= target:
                result = "win"
                pnl_pct = ((target - entry) / entry) * 100
            elif current_price <= stop:
                result = "loss"
                pnl_pct = ((stop - entry) / entry) * 100
            else:
                # Ещё в игре — считаем текущий unrealized P&L
                pnl_pct = ((current_price - entry) / entry) * 100

        elif direction == "SHORT":
            if current_price <= target:
                result = "win"
                pnl_pct = ((entry - target) / entry) * 100
            elif current_price >= stop:
                result = "loss"
                pnl_pct = ((entry - stop) / entry) * 100
            else:
                pnl_pct = ((entry - current_price) / entry) * 100

        # Истёк ли таймфрейм?
        from datetime import datetime, timedelta
        created = datetime.fromisoformat(pred["created_at"])
        tf = pred.get("timeframe", "1w")
        
        timeframe_days = {"1d": 1, "3d": 3, "1w": 7, "2w": 14, "1m": 30}
        max_days = timeframe_days.get(tf, 7)
        
        if (datetime.now() - created).days >= max_days and result == "pending":
            # Таймфрейм истёк — фиксируем текущий результат
            result = "win" if pnl_pct > 0 else "loss"

        if result in ("win", "loss"):
            await update_prediction_result(pred["id"], result, current_price, pnl_pct)
            checked += 1
            logger.info(
                f"Прогноз #{pred['id']} {pred['asset']} {direction}: "
                f"{result} | P&L: {pnl_pct:+.1f}%"
            )
        
        await asyncio.sleep(0.5)  # пауза между запросами к API

    logger.info(f"Проверено прогнозов: {checked}/{len(pending)}")
    return checked


def extract_predictions_from_report(report_text: str) -> list[dict]:
    """
    Парсит отчёт агентов и извлекает структурированные прогнозы.
    Ищет паттерны вида: "BTC: long от $96-97K, цель $105K, стоп $93K"
    """
    predictions = []
    
    # Паттерны для поиска прогнозов
    # Пример: "BTC: long от $96000, цель $105000, стоп $93000"
    pattern = re.compile(
        r'(BTC|ETH|SOL|BNB|SPY|QQQ|NVDA|AAPL|TSLA|GLD)'
        r'[:\s]+'
        r'(long|short|LONG|SHORT)'
        r'[^$]*\$\s*([\d,\.]+)[Kk]?'  # entry
        r'[^$]*(?:цел[ьи]|target)[^$]*\$\s*([\d,\.]+)[Kk]?'  # target
        r'[^$]*(?:стоп|stop)[^$]*\$\s*([\d,\.]+)[Kk]?',  # stop
        re.IGNORECASE
    )
    
    for match in pattern.finditer(report_text):
        asset, direction, entry_str, target_str, stop_str = match.groups()
        
        def parse_price(s: str) -> float:
            s = s.replace(",", "").replace(" ", "")
            val = float(s)
            # Если меньше 1000 и это крипта — умножаем на 1000 (K нотация)
            return val
        
        try:
            predictions.append({
                "asset": asset.upper(),
                "direction": direction.upper(),
                "entry_price": parse_price(entry_str),
                "target_price": parse_price(target_str),
                "stop_loss": parse_price(stop_str),
                "timeframe": "1w",  # дефолт
            })
        except ValueError:
            continue
    
    return predictions


async def save_predictions_from_report(report_text: str, source_news: str = ""):
    """Извлекает и сохраняет все прогнозы из отчёта."""
    predictions = extract_predictions_from_report(report_text)
    
    saved = 0
    for pred in predictions:
        try:
            await save_prediction(
                asset=pred["asset"],
                direction=pred["direction"],
                entry_price=pred["entry_price"],
                target_price=pred["target_price"],
                stop_loss=pred["stop_loss"],
                timeframe=pred["timeframe"],
                source_news=source_news[:300]
            )
            saved += 1
        except Exception as e:
            logger.warning(f"Не удалось сохранить прогноз: {e}")
    
    if saved:
        logger.info(f"Сохранено {saved} прогнозов из отчёта")
    
    return saved

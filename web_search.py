"""
web_search.py — Реалтайм рыночные данные.

Источники по активам:
  Крипта (BTC/ETH/SOL)  → Binance API + CoinGecko fallback
  S&P 500                → Yahoo Finance ^GSPC (индекс, не ETF SPY)
  Nasdaq 100             → Yahoo Finance ^NDX  (индекс, не ETF QQQ)
  VIX                    → Yahoo Finance ^VIX
  DXY                    → Yahoo Finance DX-Y.NYB
  Нефть WTI              → Yahoo Finance CL=F
  Золото                 → Yahoo Finance GC=F + CoinGecko fallback
                           (с sanity check: цена должна быть $1500-$5000)
  Макро (ФРС, CPI)       → FRED API
  Fear & Greed           → alternative.me

ИСПРАВЛЕНИЯ v2:
- SPY/QQQ заменены на ^GSPC/^NDX (реальные индексы вместо ETF)
- GLD*10 костыль полностью убран
- Добавлены sanity checks на все цены
- CPI пересчитывается из индекса (~327) в YoY % (~3-4%)
- Все запросы идут параллельно через asyncio.gather
"""

import asyncio
import logging
import aiohttp
from datetime import datetime
from config import FRED_API_KEY

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# База для расчёта инфляции YoY
CPI_BASE_YEAR_AGO = 314.2
FED_INFLATION_TARGET = 2.0

# Санити-чеки — разумные диапазоны цен (min, max)
PRICE_SANITY = {
    "BTC":     (10_000,  200_000),
    "ETH":     (500,     20_000),
    "SOL":     (5,       2_000),
    "SPX":     (3_000,   10_000),
    "NDX":     (8_000,   30_000),
    "VIX":     (5,       90),
    "DXY":     (80,      130),
    "OIL_WTI": (30,      200),
    "GOLD":    (1_500,   5_000),
}


def _is_sane(key: str, price: float) -> bool:
    if key not in PRICE_SANITY:
        return price > 0
    lo, hi = PRICE_SANITY[key]
    ok = lo <= price <= hi
    if not ok:
        logger.warning(f"Sanity fail [{key}]: {price} не в диапазоне {lo}-{hi}")
    return ok


# ─── Источники данных ─────────────────────────────────────────────────────────

async def _fetch_binance(session, symbol: str) -> dict | None:
    try:
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        async with session.get(url, timeout=TIMEOUT) as r:
            if r.status == 200:
                d = await r.json()
                price = float(d["lastPrice"])
                change = float(d["priceChangePercent"])
                key = symbol.replace("USDT", "")
                if _is_sane(key, price):
                    return {"price": price, "change_24h": round(change, 3), "source": "Binance"}
    except Exception as e:
        logger.debug(f"Binance {symbol}: {e}")
    return None


async def _fetch_coingecko_crypto(session) -> dict:
    result = {}
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": "bitcoin,ethereum,solana",
            "vs_currencies": "usd",
            "include_24hr_change": "true",
        }
        async with session.get(url, params=params, timeout=TIMEOUT) as r:
            if r.status == 200:
                data = await r.json()
                mapping = {"bitcoin": "BTC", "ethereum": "ETH", "solana": "SOL"}
                for cg_id, key in mapping.items():
                    if cg_id in data:
                        price = float(data[cg_id].get("usd", 0))
                        change = float(data[cg_id].get("usd_24h_change", 0))
                        if _is_sane(key, price):
                            result[key] = {
                                "price": price,
                                "change_24h": round(change, 3),
                                "source": "CoinGecko"
                            }
    except Exception as e:
        logger.debug(f"CoinGecko crypto: {e}")
    return result


async def _fetch_yahoo(session, ticker: str, key: str) -> dict | None:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        async with session.get(
            url, params={"interval": "1d", "range": "2d"}, timeout=TIMEOUT
        ) as r:
            if r.status == 200:
                data = await r.json()
                meta = data["chart"]["result"][0]["meta"]
                price = float(meta.get("regularMarketPrice", 0))
                prev  = float(meta.get("previousClose", price) or price)
                change = ((price - prev) / prev * 100) if prev else 0
                if _is_sane(key, price):
                    return {
                        "price": round(price, 2),
                        "change_24h": round(change, 3),
                        "source": f"Yahoo ({ticker})"
                    }
    except Exception as e:
        logger.debug(f"Yahoo {ticker}: {e}")
    return None


async def _fetch_gold(session) -> dict | None:
    """
    Золото — GC=F фьючерс с sanity check.
    Fallback: CoinGecko XAU.
    НЕ используем GLD ETF — он не равен цене золота.
    """
    # Попытка 1: Yahoo Finance GC=F (фьючерс на золото)
    result = await _fetch_yahoo(session, "GC=F", "GOLD")
    if result:
        logger.info(f"Золото Yahoo GC=F: ${result['price']}")
        return result

    # Попытка 2: CoinGecko XAU/USD
    try:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "gold", "vs_currencies": "usd", "include_24hr_change": "true"}
        async with session.get(url, params=params, timeout=TIMEOUT) as r:
            if r.status == 200:
                data = await r.json()
                price = float(data.get("gold", {}).get("usd", 0))
                change = float(data.get("gold", {}).get("usd_24h_change", 0))
                if _is_sane("GOLD", price):
                    logger.info(f"Золото CoinGecko: ${price}")
                    return {"price": price, "change_24h": round(change, 3), "source": "CoinGecko (XAU)"}
    except Exception as e:
        logger.debug(f"CoinGecko gold: {e}")

    logger.warning("Золото: все источники недоступны или цена вне диапазона")
    return None


async def _fetch_fred(session, series_id: str) -> str:
    if not FRED_API_KEY or FRED_API_KEY in ("", "твой_ключ", "YOUR_KEY"):
        return "N/A"
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "limit": 1,
            "sort_order": "desc",
        }
        async with session.get(url, params=params, timeout=TIMEOUT) as r:
            if r.status == 200:
                data = await r.json()
                val = data["observations"][0]["value"]
                return val if val != "." else "N/A"
    except Exception as e:
        logger.debug(f"FRED {series_id}: {e}")
    return "N/A"


async def _fetch_fear_greed(session) -> dict:
    try:
        async with session.get(
            "https://api.alternative.me/fng/?limit=2", timeout=TIMEOUT
        ) as r:
            if r.status == 200:
                d = await r.json()
                items = d.get("data", [])
                if items:
                    cur = items[0]
                    prev_val = int(items[1]["value"]) if len(items) > 1 else int(cur["value"])
                    val = int(cur["value"])
                    return {
                        "val": val,
                        "status": cur["value_classification"],
                        "change": val - prev_val,
                    }
    except Exception as e:
        logger.debug(f"Fear&Greed: {e}")
    return {"val": "N/A", "status": "Unknown", "change": 0}


# ─── Главный агрегатор ────────────────────────────────────────────────────────

async def fetch_realtime_prices() -> dict:
    prices = {}

    async with aiohttp.ClientSession(headers=HEADERS) as session:

        # Все запросы параллельно
        results = await asyncio.gather(
            _fetch_binance(session, "BTCUSDT"),
            _fetch_binance(session, "ETHUSDT"),
            _fetch_binance(session, "SOLUSDT"),
            _fetch_yahoo(session, "^GSPC",    "SPX"),   # S&P 500 индекс
            _fetch_yahoo(session, "^NDX",     "NDX"),   # Nasdaq 100 индекс
            _fetch_yahoo(session, "^VIX",     "VIX"),
            _fetch_yahoo(session, "DX-Y.NYB", "DXY"),
            _fetch_yahoo(session, "CL=F",     "OIL_WTI"),
            _fetch_gold(session),
            _fetch_fred(session, "FEDFUNDS"),
            _fetch_fred(session, "CPIAUCSL"),
            _fetch_fear_greed(session),
            return_exceptions=True,
        )

        (
            btc, eth, sol,
            spx, ndx, vix, dxy, oil,
            gold,
            fed_rate, cpi_raw,
            fng,
        ) = results

        # Крипта — Binance с CoinGecko fallback
        crypto_keys = [("BTC", btc), ("ETH", eth), ("SOL", sol)]
        missing = [k for k, v in crypto_keys if not v or isinstance(v, Exception)]
        cg = {}
        if missing:
            logger.warning(f"Binance не дал: {missing}, пробую CoinGecko")
            cg = await _fetch_coingecko_crypto(session)

        for key, val in crypto_keys:
            if val and not isinstance(val, Exception):
                prices[key] = val
            elif key in cg:
                prices[key] = cg[key]

        # Индексы и сырьё
        for key, val in [
            ("SPX", spx), ("NDX", ndx), ("VIX", vix),
            ("DXY", dxy), ("OIL_WTI", oil), ("GOLD", gold),
        ]:
            if val and not isinstance(val, Exception):
                prices[key] = val

        # Макро
        prices["MACRO"] = {
            "fed_rate": fed_rate if not isinstance(fed_rate, Exception) else "N/A",
            "cpi_raw":  cpi_raw  if not isinstance(cpi_raw, Exception) else "N/A",
            "fng":      fng      if not isinstance(fng, Exception) else {"val": "N/A", "status": "Unknown", "change": 0},
        }

    got     = [k for k in prices if k != "MACRO"]
    missing = [k for k in ["BTC", "ETH", "SPX", "NDX", "GOLD", "OIL_WTI"] if k not in prices]
    logger.info(f"Цены получены: {got}")
    if missing:
        logger.warning(f"Цены НЕ получены: {missing}")

    return prices


# ─── CPI → YoY % ──────────────────────────────────────────────────────────────

def _cpi_to_yoy(cpi_raw_str: str) -> str:
    try:
        cpi_value = float(cpi_raw_str)
        yoy_pct = ((cpi_value - CPI_BASE_YEAR_AGO) / CPI_BASE_YEAR_AGO) * 100
        gap = yoy_pct - FED_INFLATION_TARGET
        gap_str = f"+{gap:.1f}%" if gap > 0 else f"{gap:.1f}%"
        if gap > 1.0:
            status = "выше таргета ФРС"
        elif gap > 0.3:
            status = "незначительно выше таргета"
        else:
            status = "близко к таргету ФРС"
        return (
            f"~{yoy_pct:.1f}% годовых (YoY) — {status} "
            f"(таргет 2.0%, отклонение {gap_str})"
        )
    except (ValueError, TypeError):
        return "нет данных"


# ─── Форматирование для агентов ───────────────────────────────────────────────

def format_prices_for_agents(prices: dict) -> str:
    if not prices:
        return "Актуальные рыночные данные временно недоступны."

    now = datetime.now().strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"=== ВЕРИФИЦИРОВАННЫЕ РЫНОЧНЫЕ ДАННЫЕ ({now}) ==="]

    # Крипта
    lines.append("\n[КРИПТОРЫНОК]")
    for k, label in [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana")]:
        if k in prices:
            p = prices[k]
            ch = p["change_24h"]
            arrow = "🟢" if ch >= 0 else "🔴"
            lines.append(
                f"  {label} ({k}): ${p['price']:,.2f} "
                f"{arrow} {ch:+.2f}% за 24ч  [{p['source']}]"
            )

    # Макро
    if "MACRO" in prices:
        m = prices["MACRO"]
        fng = m.get("fng", {})
        fng_val    = fng.get("val", "N/A")
        fng_status = fng.get("status", "")
        fng_change = fng.get("change", 0)
        fng_arrow  = "🟢" if fng_change > 0 else ("🔴" if fng_change < 0 else "➡️")
        lines.append("\n[МАКРОЭКОНОМИКА США]")
        lines.append(f"  Ставка ФРС: {m['fed_rate']}%  [FRED]")
        lines.append(f"  Инфляция CPI: {_cpi_to_yoy(m.get('cpi_raw', 'N/A'))}  [FRED]")
        lines.append(
            f"  Fear & Greed (крипта): {fng_val}/100 ({fng_status}) "
            f"{fng_arrow} {fng_change:+d} за сутки"
        )
        lines.append(
            "  [!] CPI = индекс уровня цен, НЕ процент инфляции. "
            "Инфляция = YoY изменение (указано выше)."
        )

    # Фондовые индексы
    lines.append("\n[ФОНДОВЫЕ ИНДЕКСЫ]")
    for k, label in [
        ("SPX", "S&P 500 (^GSPC)"),
        ("NDX", "Nasdaq 100 (^NDX)"),
        ("VIX", "VIX — волатильность"),
    ]:
        if k in prices:
            p = prices[k]
            ch = p["change_24h"]
            arrow = "🟢" if ch >= 0 else "🔴"
            lines.append(
                f"  {label}: {p['price']:,.2f} "
                f"{arrow} {ch:+.2f}%  [{p['source']}]"
            )

    # Сырьё и валюты
    lines.append("\n[СЫРЬЁ И ВАЛЮТЫ]")
    for k, label, unit in [
        ("OIL_WTI", "Нефть WTI",     "$/барр"),
        ("GOLD",    "Золото",         "$/унц"),
        ("DXY",     "Индекс доллара", ""),
    ]:
        if k in prices:
            p = prices[k]
            ch = p["change_24h"]
            arrow = "🟢" if ch >= 0 else "🔴"
            unit_str = f" {unit}" if unit else ""
            lines.append(
                f"  {label}: {p['price']:,.2f}{unit_str} "
                f"{arrow} {ch:+.2f}%  [{p['source']}]"
            )

    lines.append(
        "\n⚠️ ИНСТРУКЦИЯ АГЕНТАМ: используй ТОЛЬКО эти цифры. "
        "Не выдумывай цены. Если актива нет — пиши 'нет данных'."
    )
    return "\n".join(lines)


# ─── Вспомогательные функции ──────────────────────────────────────────────────

async def search_ddg(query: str) -> str:
    try:
        url = "https://api.duckduckgo.com/"
        params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, params=params, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json(content_type=None)
        answer = (
            data.get("Answer")
            or data.get("AbstractText")
            or data.get("Definition")
            or ""
        )
        return answer[:400] if answer else ""
    except Exception as e:
        logger.debug(f"DDG: {e}")
        return ""


async def search_brave(query: str) -> str:
    return ""


async def search_news_context(topic: str) -> str:
    queries = [f"{topic} latest market news today", f"{topic} analysis 2025"]
    results = []
    for q in queries:
        ans = await search_ddg(q)
        if ans:
            results.append(ans)
    return "\n\n".join(results) if results else "Свежих новостей по теме не найдено."


async def get_full_realtime_context() -> tuple[dict, str]:
    """Точка входа для основного бота."""
    prices = await fetch_realtime_prices()
    formatted = format_prices_for_agents(prices)
    return prices, formatted

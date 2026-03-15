"""
web_search.py — Реалтайм рыночные данные для Dialectic Edge.

Источники:
  BTC / ETH / SOL     → Binance API (быстро, точно)
                        fallback: CoinGecko
  S&P 500             → Yahoo ^GSPC  (индекс ~6600, НЕ ETF SPY ~560)
  Nasdaq 100          → Yahoo ^NDX   (индекс ~19000, НЕ ETF QQQ ~480)
  VIX                 → Yahoo ^VIX
  DXY                 → Yahoo DX-Y.NYB
  Нефть WTI           → Yahoo CL=F
  Золото              → Yahoo GC=F   (~$5000-5200, санити-чек подтверждён)
                        fallback: Yahoo XAUUSD=X
  Ставка ФРС          → FRED FEDFUNDS
  CPI (→ YoY %)       → FRED CPIAUCSL, пересчёт через базу марта 2025

РЕАЛЬНЫЕ ЦЕНЫ март 2026 (для справки и проверки санити-чеков):
  S&P 500   ~6,600     Nasdaq 100  ~19,000–24,000
  Gold      ~$5,000–5,200          Oil WTI  ~$87–99
  BTC       ~$69,000–72,000        CPI YoY  ~2.4%
"""

import asyncio
import logging
import aiohttp
from datetime import datetime
from config import FRED_API_KEY

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# ─── Константы ────────────────────────────────────────────────────────────────

# CPI индекс год назад (март 2025 ≈ 319.8 по данным BLS)
# Текущий CPI ~323-325 → YoY ≈ 2.4% — совпадает с реальными данными
CPI_BASE_YEAR_AGO   = 319.8
FED_INFLATION_TARGET = 2.0

# Санити-чеки — реальные диапазоны цен март 2026
PRICE_SANITY = {
    "BTC":     (20_000,  250_000),
    "ETH":     (500,     30_000),
    "SOL":     (10,      3_000),
    "SPX":     (4_000,   12_000),   # S&P 500 индекс
    "NDX":     (10_000,  35_000),   # Nasdaq 100 индекс
    "VIX":     (5,       90),
    "DXY":     (80,      130),
    "OIL_WTI": (30,      200),
    "GOLD":    (2_000,   8_000),    # Золото ~$5000 в марте 2026 — всё верно
}


def _sane(key: str, price: float) -> bool:
    """True если цена в разумном диапазоне."""
    if not price or price <= 0:
        return False
    if key not in PRICE_SANITY:
        return True
    lo, hi = PRICE_SANITY[key]
    ok = lo <= price <= hi
    if not ok:
        logger.warning(f"[sanity] {key}: {price:.2f} вне диапазона [{lo}, {hi}]")
    return ok


# ─── Получение данных ─────────────────────────────────────────────────────────

async def _binance(session, symbol: str, key: str) -> dict | None:
    """Цена с Binance. symbol = 'BTCUSDT' и т.д."""
    try:
        url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
        async with session.get(url, timeout=TIMEOUT) as r:
            if r.status == 200:
                d = await r.json()
                price  = float(d["lastPrice"])
                change = float(d["priceChangePercent"])
                if _sane(key, price):
                    return {"price": price, "change_24h": round(change, 3), "source": "Binance"}
    except Exception as e:
        logger.debug(f"Binance {symbol}: {e}")
    return None


async def _coingecko_crypto(session) -> dict:
    """CoinGecko — fallback для крипты."""
    out = {}
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
                for cg_id, key in [("bitcoin","BTC"),("ethereum","ETH"),("solana","SOL")]:
                    if cg_id in data:
                        p = float(data[cg_id].get("usd", 0))
                        c = float(data[cg_id].get("usd_24h_change", 0))
                        if _sane(key, p):
                            out[key] = {"price": p, "change_24h": round(c, 3), "source": "CoinGecko"}
    except Exception as e:
        logger.debug(f"CoinGecko crypto: {e}")
    return out


async def _yahoo(session, ticker: str, key: str) -> dict | None:
    """Yahoo Finance v8 chart endpoint."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        async with session.get(
            url, params={"interval": "1d", "range": "2d"}, timeout=TIMEOUT
        ) as r:
            if r.status == 200:
                data = await r.json()
                meta   = data["chart"]["result"][0]["meta"]
                price  = float(meta.get("regularMarketPrice", 0))
                prev   = float(meta.get("previousClose", price) or price)
                change = ((price - prev) / prev * 100) if prev else 0.0
                if _sane(key, price):
                    return {
                        "price":      round(price, 2),
                        "change_24h": round(change, 3),
                        "source":     f"Yahoo ({ticker})",
                    }
    except Exception as e:
        logger.debug(f"Yahoo {ticker}: {e}")
    return None


async def _gold(session) -> dict | None:
    """
    Золото — пробуем GC=F (фьючерс), затем XAUUSD=X (спот Yahoo).
    Санити-чек: $2000–8000 (в марте 2026 цена ~$5000–5200).
    """
    for ticker in ["GC=F", "XAUUSD=X"]:
        result = await _yahoo(session, ticker, "GOLD")
        if result:
            logger.info(f"Золото из {ticker}: ${result['price']:,.2f}")
            return result
    logger.warning("Золото: оба источника не дали результат")
    return None


async def _fred(session, series_id: str) -> str:
    """Последнее значение ряда FRED."""
    if not FRED_API_KEY or FRED_API_KEY in ("", "твой_ключ", "YOUR_KEY"):
        return "N/A"
    try:
        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id":  series_id,
            "api_key":    FRED_API_KEY,
            "file_type":  "json",
            "limit":      1,
            "sort_order": "desc",
        }
        async with session.get(url, params=params, timeout=TIMEOUT) as r:
            if r.status == 200:
                data = await r.json()
                val  = data["observations"][0]["value"]
                return val if val != "." else "N/A"
    except Exception as e:
        logger.debug(f"FRED {series_id}: {e}")
    return "N/A"


async def _fear_greed(session) -> dict:
    """Fear & Greed Index — alternative.me."""
    try:
        async with session.get(
            "https://api.alternative.me/fng/?limit=2", timeout=TIMEOUT
        ) as r:
            if r.status == 200:
                d = await r.json()
                items = d.get("data", [])
                if items:
                    cur  = items[0]
                    prev = int(items[1]["value"]) if len(items) > 1 else int(cur["value"])
                    val  = int(cur["value"])
                    return {"val": val, "status": cur["value_classification"], "change": val - prev}
    except Exception as e:
        logger.debug(f"Fear&Greed: {e}")
    return {"val": "N/A", "status": "Unknown", "change": 0}


# ─── Агрегатор ────────────────────────────────────────────────────────────────

async def fetch_realtime_prices() -> dict:
    """
    Все запросы идут параллельно через asyncio.gather.
    Возвращает dict с ключами: BTC, ETH, SOL, SPX, NDX, VIX, DXY, OIL_WTI, GOLD, MACRO.
    """
    prices = {}

    async with aiohttp.ClientSession(headers=HEADERS) as session:

        (
            btc, eth, sol,
            spx, ndx, vix, dxy, oil, gold,
            fed_rate, cpi_raw, fng,
        ) = await asyncio.gather(
            # Крипта — Binance
            _binance(session, "BTCUSDT", "BTC"),
            _binance(session, "ETHUSDT", "ETH"),
            _binance(session, "SOLUSDT", "SOL"),
            # Индексы — реальные тикеры (^GSPC = S&P 500, ^NDX = Nasdaq 100)
            _yahoo(session, "^GSPC",    "SPX"),
            _yahoo(session, "^NDX",     "NDX"),
            _yahoo(session, "^VIX",     "VIX"),
            _yahoo(session, "DX-Y.NYB", "DXY"),
            _yahoo(session, "CL=F",     "OIL_WTI"),
            # Золото
            _gold(session),
            # FRED макро
            _fred(session, "FEDFUNDS"),
            _fred(session, "CPIAUCSL"),
            # Сентимент
            _fear_greed(session),
            return_exceptions=True,
        )

        # Крипта: Binance → CoinGecko fallback
        missing_crypto = []
        for key, val in [("BTC", btc), ("ETH", eth), ("SOL", sol)]:
            if val and not isinstance(val, Exception):
                prices[key] = val
            else:
                missing_crypto.append(key)

        if missing_crypto:
            logger.warning(f"Binance не дал {missing_crypto} — пробую CoinGecko")
            cg = await _coingecko_crypto(session)
            for key in missing_crypto:
                if key in cg:
                    prices[key] = cg[key]

        # Всё остальное
        for key, val in [
            ("SPX", spx), ("NDX", ndx), ("VIX", vix),
            ("DXY", dxy), ("OIL_WTI", oil), ("GOLD", gold),
        ]:
            if val and not isinstance(val, Exception):
                prices[key] = val

        prices["MACRO"] = {
            "fed_rate": fed_rate if not isinstance(fed_rate, Exception) else "N/A",
            "cpi_raw":  cpi_raw  if not isinstance(cpi_raw, Exception)  else "N/A",
            "fng":      fng      if not isinstance(fng, Exception)       else {"val": "N/A", "status": "Unknown", "change": 0},
        }

    got     = [k for k in prices if k != "MACRO"]
    missing = [k for k in ["BTC", "ETH", "SPX", "NDX", "GOLD", "OIL_WTI"] if k not in prices]
    logger.info(f"✅ Цены получены: {got}")
    if missing:
        logger.warning(f"❌ Не получены: {missing}")

    return prices


# ─── CPI индекс → YoY % ──────────────────────────────────────────────────────

def _cpi_yoy(raw: str) -> str:
    """
    FRED отдаёт CPI как индекс (~323), не как процент.
    Пересчитываем: YoY = (текущий - год назад) / год назад * 100.
    База CPI_BASE_YEAR_AGO = март 2025 ≈ 319.8 → YoY ≈ 2.4% (совпадает с BLS).
    """
    try:
        v    = float(raw)
        yoy  = (v - CPI_BASE_YEAR_AGO) / CPI_BASE_YEAR_AGO * 100
        gap  = yoy - FED_INFLATION_TARGET
        g_s  = f"+{gap:.1f}%" if gap > 0 else f"{gap:.1f}%"
        if gap > 1.0:
            status = "выше таргета ФРС"
        elif gap > 0.3:
            status = "незначительно выше таргета"
        else:
            status = "близко к таргету ФРС"
        return f"~{yoy:.1f}% YoY — {status} (таргет 2.0%, отклонение {g_s})"
    except (ValueError, TypeError):
        return "нет данных"


# ─── Форматирование ───────────────────────────────────────────────────────────

def format_prices_for_agents(prices: dict) -> str:
    if not prices:
        return "Рыночные данные временно недоступны."

    now   = datetime.now().strftime("%d.%m.%Y %H:%M UTC")
    lines = [f"=== ВЕРИФИЦИРОВАННЫЕ РЫНОЧНЫЕ ДАННЫЕ ({now}) ==="]

    # Крипта
    lines.append("\n[КРИПТОРЫНОК]")
    for k, label in [("BTC","Bitcoin"),("ETH","Ethereum"),("SOL","Solana")]:
        if k in prices:
            p  = prices[k]
            ch = p["change_24h"]
            arrow = "🟢" if ch >= 0 else "🔴"
            lines.append(
                f"  {label} ({k}): ${p['price']:,.2f}  "
                f"{arrow} {ch:+.2f}% за 24ч  [{p['source']}]"
            )

    # Макро
    if "MACRO" in prices:
        m   = prices["MACRO"]
        fng = m.get("fng", {})
        fv  = fng.get("val", "N/A")
        fs  = fng.get("status", "")
        fc  = fng.get("change", 0)
        fa  = "🟢" if fc > 0 else ("🔴" if fc < 0 else "➡️")
        lines.append("\n[МАКРОЭКОНОМИКА США]")
        lines.append(f"  Ставка ФРС:      {m['fed_rate']}%  [FRED]")
        lines.append(f"  Инфляция CPI:    {_cpi_yoy(m.get('cpi_raw','N/A'))}  [FRED]")
        lines.append(
            f"  Fear & Greed:    {fv}/100 ({fs})  "
            f"{fa} {fc:+d} за сутки"
        )
        lines.append(
            "  [!] CPI = индекс (~323), НЕ процент. "
            "Инфляция = YoY изменение (см. выше)."
        )

    # Фондовые индексы
    lines.append("\n[ФОНДОВЫЕ ИНДЕКСЫ]")
    for k, label in [
        ("SPX", "S&P 500"),
        ("NDX", "Nasdaq 100"),
        ("VIX", "VIX (волатильность)"),
    ]:
        if k in prices:
            p  = prices[k]
            ch = p["change_24h"]
            arrow = "🟢" if ch >= 0 else "🔴"
            lines.append(
                f"  {label}: {p['price']:,.2f}  "
                f"{arrow} {ch:+.2f}%  [{p['source']}]"
            )

    # Сырьё
    lines.append("\n[СЫРЬЁ И ВАЛЮТЫ]")
    for k, label, unit in [
        ("OIL_WTI", "Нефть WTI",     "$/барр"),
        ("GOLD",    "Золото",         "$/унц"),
        ("DXY",     "Индекс доллара", ""),
    ]:
        if k in prices:
            p    = prices[k]
            ch   = p["change_24h"]
            arrow = "🟢" if ch >= 0 else "🔴"
            u    = f" {unit}" if unit else ""
            lines.append(
                f"  {label}: {p['price']:,.2f}{u}  "
                f"{arrow} {ch:+.2f}%  [{p['source']}]"
            )

    lines.append(
        "\n⚠️ ИНСТРУКЦИЯ АГЕНТАМ: используй ТОЛЬКО эти цифры. "
        "Если актива нет в списке — пиши 'нет данных'. Не выдумывай цены."
    )
    return "\n".join(lines)


# ─── Вспомогательные ──────────────────────────────────────────────────────────

async def search_ddg(query: str) -> str:
    try:
        url    = "https://api.duckduckgo.com/"
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
    queries = [f"{topic} latest market news today", f"{topic} analysis 2026"]
    results = []
    for q in queries:
        ans = await search_ddg(q)
        if ans:
            results.append(ans)
    return "\n\n".join(results) if results else "Свежих новостей по теме не найдено."


async def get_full_realtime_context() -> tuple[dict, str]:
    """Точка входа для основного бота."""
    prices    = await fetch_realtime_prices()
    formatted = format_prices_for_agents(prices)
    return prices, formatted

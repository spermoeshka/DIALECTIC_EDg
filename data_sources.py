"""
data_sources.py — Расширенные источники данных для глубокого анализа.

Бесплатные источники:
1. GDELT — геополитические события в реальном времени
2. Fed Calendar — расписание заседаний ФРС и ЕЦБ
3. Fear & Greed Index — сентимент крипто и фондового рынка
4. Макро-данные — инфляция, ВВП, занятость (FRED API)
5. Commodity prices — нефть, золото, медь, газ
6. Whale Alert — крупные on-chain транзакции крипты
7. SEC Filings — инсайдерские покупки/продажи акций (легально, публично)
8. Earnings Calendar — отчётности компаний
9. Options Flow — необычная активность опционов
10. Social Sentiment — тренды в финансовых сообществах
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
import aiohttp
import json

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=12)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DialecticEdge/2.0)"}


# ─── 1. ГЕОПОЛИТИКА — GDELT ───────────────────────────────────────────────────

async def fetch_geopolitical_events() -> str:
    """
    GDELT Project — крупнейшая база геополитических событий.
    Обновляется каждые 15 минут, полностью бесплатно.
    Покрывает: войны, санкции, выборы, дипломатия, протесты.
    """
    try:
        # GDELT DOC API — топ статьи по теме экономика/геополитика
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {
            "query": "economy sanctions war trade geopolitics",
            "mode": "artlist",
            "maxrecords": 10,
            "format": "json",
            "timespan": "24h",
            "sort": "hybridrel",
        }
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, params=params, timeout=TIMEOUT) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json(content_type=None)

        articles = data.get("articles", [])
        if not articles:
            return ""

        lines = ["🌍 *ГЕОПОЛИТИКА (GDELT):*"]
        for art in articles[:6]:
            title = art.get("title", "")[:120]
            source = art.get("domain", "")
            if title:
                lines.append(f"• {title} _({source})_")

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"GDELT error: {e}")
        return ""


# ─── 2. МАКРО — FRED API (Federal Reserve Economic Data) ─────────────────────

async def fetch_macro_indicators() -> str:
    """
    FRED — официальная база данных ФРС США.
    Содержит 800,000+ экономических показателей.
    Бесплатно без ключа для основных показателей.
    """
    try:
        # Ключевые макро-индикаторы
        indicators = {
            "FEDFUNDS": "Ставка ФРС %",
            "CPIAUCSL": "Инфляция CPI (США)",
            "UNRATE": "Безработица США %",
            "DGS10": "Доходность 10-лет US Treasury",
            "DXY": "Индекс доллара DXY",
        }

        results = {}
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            for series_id, name in list(indicators.items())[:4]:
                try:
                    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
                    async with session.get(url, timeout=TIMEOUT) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            lines = [l for l in text.strip().split("\n") if l]
                            if len(lines) >= 2:
                                last_line = lines[-1].split(",")
                                if len(last_line) == 2:
                                    date_str = last_line[0].strip()
                                    value = last_line[1].strip()
                                    if value and value != ".":
                                        results[name] = (float(value), date_str)
                    await asyncio.sleep(0.3)
                except Exception:
                    continue

        if not results:
            return ""

        lines = ["📊 *МАКРОЭКОНОМИКА (FRED/ФРС):*"]
        for name, (value, date) in results.items():
            lines.append(f"• {name}: *{value:.2f}* _(на {date})_")

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"FRED error: {e}")
        return ""


# ─── 3. FEAR & GREED INDEX ────────────────────────────────────────────────────

async def fetch_fear_greed() -> str:
    """
    CNN Fear & Greed Index для акций + Crypto Fear & Greed.
    Ключевой сентимент-индикатор — экстремальный страх = время покупать.
    """
    results = []

    # Crypto Fear & Greed
    try:
        url = "https://api.alternative.me/fng/?limit=2&format=json"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("data", [])
                    if items:
                        current = items[0]
                        value = int(current.get("value", 0))
                        label = current.get("value_classification", "")
                        yesterday = int(items[1].get("value", 0)) if len(items) > 1 else value
                        change = value - yesterday

                        # Интерпретация
                        if value <= 25:
                            signal = "🔴 Экстремальный страх — исторически точка входа"
                        elif value <= 45:
                            signal = "🟠 Страх — рынок осторожен"
                        elif value <= 55:
                            signal = "🟡 Нейтрально"
                        elif value <= 75:
                            signal = "🟢 Жадность — осторожно"
                        else:
                            signal = "🔴 Экстремальная жадность — риск коррекции"

                        change_str = f"+{change}" if change > 0 else str(change)
                        results.append(
                            f"₿ Crypto Fear & Greed: *{value}/100* ({label}) "
                            f"{change_str} за сутки\n   {signal}"
                        )
    except Exception as e:
        logger.warning(f"Crypto F&G error: {e}")

    if not results:
        return ""

    return "😱 *ИНДЕКС СТРАХА И ЖАДНОСТИ:*\n" + "\n".join(results)


# ─── 4. COMMODITIES — СЫРЬЕВЫЕ ТОВАРЫ ────────────────────────────────────────

async def fetch_commodities() -> str:
    """
    Цены на ключевые commodities через Yahoo Finance.
    Нефть, золото, медь, газ, пшеница — опережающие индикаторы экономики.
    """
    commodities = {
        "CL=F":  ("🛢️ Нефть WTI", "$/баррель"),
        "GC=F":  ("🥇 Золото", "$/унция"),
        "SI=F":  ("🥈 Серебро", "$/унция"),
        "HG=F":  ("🔶 Медь", "$/фунт"),  # Dr.Copper — предсказывает экономику
        "NG=F":  ("🔥 Газ", "$/MMBtu"),
        "ZW=F":  ("🌾 Пшеница", "$/бушель"),
        "DX-Y.NYB": ("💵 Индекс доллара", ""),
    }

    results = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for ticker, (name, unit) in commodities.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                params = {"interval": "1d", "range": "2d"}
                async with session.get(url, params=params, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        meta = data["chart"]["result"][0]["meta"]
                        price = meta.get("regularMarketPrice", 0)
                        prev = meta.get("previousClose", price)
                        if price and prev:
                            change = ((price - prev) / prev) * 100
                            ch_emoji = "🟢" if change >= 0 else "🔴"
                            ch_str = f"+{change:.1f}%" if change >= 0 else f"{change:.1f}%"
                            results.append(
                                f"{name}: *{price:.2f}* {unit} {ch_emoji} {ch_str}"
                            )
                await asyncio.sleep(0.2)
            except Exception:
                continue

    if not results:
        return ""

    # Добавляем интерпретацию меди (Dr. Copper)
    interpretation = []
    copper_line = next((r for r in results if "Медь" in r), None)
    if copper_line and "🔴" in copper_line:
        interpretation.append("⚠️ _Медь падает → сигнал замедления мировой экономики_")
    elif copper_line and "🟢" in copper_line:
        interpretation.append("✅ _Медь растёт → сигнал роста промышленного спроса_")

    lines = ["🛢️ *СЫРЬЕВЫЕ ТОВАРЫ:*"] + results + interpretation
    return "\n".join(lines)


# ─── 5. ИНСАЙДЕРСКИЕ СДЕЛКИ SEC (легально, публично) ─────────────────────────

async def fetch_sec_insider_trades() -> str:
    """
    SEC Form 4 — законные публичные данные об инсайдерских покупках/продажах.
    Когда CEO покупает акции своей компании на личные деньги — это бычий сигнал.
    Данные публикуются через 2 дня после сделки.
    НЕ нарушает закон — это публичная информация которую обязаны раскрывать.
    """
    try:
        # OpenInsider — агрегатор SEC Form 4 (бесплатно)
        url = "https://openinsider.com/screener"
        params = {
            "s": "",           # любой тикер
            "o": "",
            "pl": "1000000",   # минимум $1M сделки
            "ph": "",
            "yn": "1",         # только покупки
            "sortcol": "0",
            "cnt": "10",
            "action": "getdata",
        }

        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, params=params, timeout=TIMEOUT) as resp:
                # OpenInsider возвращает HTML — парсим базово
                if resp.status != 200:
                    return ""
                text = await resp.text()

        # Простой парсинг таблицы
        import re
        rows = re.findall(
            r'<td[^>]*>\s*([A-Z]{1,5})\s*</td>.*?'
            r'<td[^>]*>\s*(CEO|CFO|Director|President|COO|CTO)\s*</td>.*?'
            r'<td[^>]*>\s*\+?([\d,]+)\s*</td>',
            text, re.DOTALL
        )

        if not rows:
            return ""

        lines = ["🏛️ *ИНСАЙДЕРСКИЕ ПОКУПКИ (SEC Form 4):*"]
        lines.append("_Топ-менеджеры покупают акции своих компаний на личные деньги:_")

        seen = set()
        count = 0
        for ticker, role, shares in rows[:8]:
            if ticker not in seen and count < 5:
                seen.add(ticker)
                shares_fmt = shares.replace(",", "")
                try:
                    shares_int = int(shares_fmt)
                    if shares_int > 1000:
                        lines.append(f"• *{ticker}* — {role} купил {shares:} акций")
                        count += 1
                except ValueError:
                    continue

        if count == 0:
            return ""

        lines.append("_⚠️ Инсайдерские покупки — сигнал уверенности, не гарантия роста_")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"SEC insider error: {e}")
        return ""


# ─── 6. ЭКОНОМИЧЕСКИЙ КАЛЕНДАРЬ ───────────────────────────────────────────────

async def fetch_economic_calendar() -> str:
    """
    Важные экономические события на ближайшие 7 дней.
    Заседания ФРС, данные по инфляции, NFP, ВВП.
    Источник: Investing.com RSS (бесплатно).
    """
    try:
        import feedparser

        url = "https://www.investing.com/rss/news_14.rss"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=TIMEOUT) as resp:
                content = await resp.text()

        feed = feedparser.parse(content)

        # Ключевые события которые двигают рынок
        keywords = [
            "fed", "fomc", "rate decision", "cpi", "inflation", "nfp",
            "jobs report", "gdp", "payroll", "ecb", "bank of england",
            "powell", "lagarde", "interest rate", "ставка", "заседание"
        ]

        important = []
        for entry in feed.entries[:20]:
            title = entry.get("title", "").lower()
            if any(kw in title for kw in keywords):
                important.append(entry.get("title", "")[:100])
            if len(important) >= 4:
                break

        if not important:
            return ""

        lines = ["📅 *ВАЖНЫЕ СОБЫТИЯ (Экономический календарь):*"]
        for event in important:
            lines.append(f"• {event}")

        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"Economic calendar error: {e}")
        return ""


# ─── 7. ON-CHAIN МЕТРИКИ (крипта) ─────────────────────────────────────────────

async def fetch_onchain_metrics() -> str:
    """
    Glassnode публичные метрики + CoinGecko on-chain данные.
    Крупные транзакции, активные адреса, exchange flows.
    """
    try:
        results = []

        async with aiohttp.ClientSession() as session:
            # Bitcoin on-chain через blockchain.info (бесплатно)
            try:
                url = "https://blockchain.info/stats?format=json"
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        n_tx = data.get("n_tx", 0)
                        mempool = data.get("mempool_size", 0)
                        hash_rate = data.get("hash_rate", 0)

                        results.append(f"• Транзакций BTC за 24ч: *{n_tx:,}*")
                        results.append(f"• Mempool (незакрытых): *{mempool:,}*")
                        if hash_rate:
                            results.append(f"• Hash Rate: *{hash_rate/1e9:.1f} EH/s*")
            except Exception:
                pass

            # ETH gas price
            try:
                url = "https://api.etherscan.io/api?module=gastracker&action=gasoracle"
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        result = data.get("result", {})
                        safe_gas = result.get("SafeGasPrice", "?")
                        results.append(f"• ETH Gas (safe): *{safe_gas} Gwei*")
            except Exception:
                pass

        if not results:
            return ""

        lines = ["⛓️ *ON-CHAIN МЕТРИКИ:*"] + results
        # Интерпретация
        lines.append("_Высокий mempool = сеть перегружена, высокий спрос_")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"On-chain error: {e}")
        return ""


# ─── 8. ГЛОБАЛЬНЫЕ РЫНКИ — АЗИЯ И ЕВРОПА ─────────────────────────────────────

async def fetch_global_markets() -> str:
    """
    Индексы мировых рынков — важный контекст для понимания глобального сентимента.
    Азия открывается первой — даёт сигнал для Европы и США.
    """
    indices = {
        "^N225":  "🇯🇵 Nikkei 225",
        "^HSI":   "🇭🇰 Hang Seng",
        "^SSEC":  "🇨🇳 Shanghai",
        "^FTSE":  "🇬🇧 FTSE 100",
        "^GDAXI": "🇩🇪 DAX",
        "^FCHI":  "🇫🇷 CAC 40",
        "^RTS.ME":"🇷🇺 RTS (Россия)",
    }

    results = []
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for ticker, name in list(indices.items())[:5]:
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
                params = {"interval": "1d", "range": "2d"}
                async with session.get(url, params=params, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        meta = data["chart"]["result"][0]["meta"]
                        price = meta.get("regularMarketPrice", 0)
                        prev = meta.get("previousClose", price)
                        if price and prev:
                            change = ((price - prev) / prev) * 100
                            ch_emoji = "🟢" if change >= 0 else "🔴"
                            ch_str = f"+{change:.1f}%" if change >= 0 else f"{change:.1f}%"
                            results.append(f"{name}: {ch_emoji} {ch_str}")
                await asyncio.sleep(0.2)
            except Exception:
                continue

    if not results:
        return ""

    # Считаем общий сентимент
    green = sum(1 for r in results if "🟢" in r)
    red = sum(1 for r in results if "🔴" in r)
    if green > red:
        sentiment = "🟢 _Глобальный риск-аппетит позитивный_"
    elif red > green:
        sentiment = "🔴 _Глобальное бегство от риска_"
    else:
        sentiment = "🟡 _Смешанный глобальный сентимент_"

    lines = ["🌐 *МИРОВЫЕ РЫНКИ:*"] + results + [sentiment]
    return "\n".join(lines)


# ─── 9. SOCIAL SENTIMENT — ТРЕНДЫ ────────────────────────────────────────────

async def fetch_trending_topics() -> str:
    """
    Трендовые финансовые темы из Reddit и публичных источников.
    CoinGecko trending — что ищут люди прямо сейчас.
    """
    results = []

    try:
        # CoinGecko trending coins
        url = "https://api.coingecko.com/api/v3/search/trending"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    coins = data.get("coins", [])[:5]
                    if coins:
                        trending = [c["item"]["name"] for c in coins]
                        results.append(
                            "🔥 *Trending крипта (CoinGecko):* " +
                            " | ".join(trending)
                        )
    except Exception:
        pass

    return "\n".join(results) if results else ""


# ─── ГЛАВНАЯ ФУНКЦИЯ — СОБИРАЕМ ВСЁ ──────────────────────────────────────────

async def fetch_full_context() -> str:
    """
    Параллельно собирает все источники данных.
    Возвращает единый контекст для агентов.
    """
    logger.info("📡 Собираю расширенный контекст данных...")

    tasks = [
        fetch_geopolitical_events(),
        fetch_macro_indicators(),
        fetch_fear_greed(),
        fetch_commodities(),
        fetch_global_markets(),
        fetch_economic_calendar(),
        fetch_onchain_metrics(),
        fetch_sec_insider_trades(),
        fetch_trending_topics(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    sections = []
    labels = [
        "Геополитика", "Макро", "Сентимент", "Сырьё",
        "Мировые рынки", "Календарь", "On-chain",
        "Инсайдеры SEC", "Тренды"
    ]

    for label, result in zip(labels, results):
        if isinstance(result, str) and result.strip():
            sections.append(result)
        elif isinstance(result, Exception):
            logger.warning(f"{label}: {result}")

    if not sections:
        return "Расширенные данные временно недоступны."

    now = datetime.now().strftime("%d.%m.%Y %H:%M UTC")
    header = f"=== РАСШИРЕННЫЙ КОНТЕКСТ ({now}) ===\n"

    return header + "\n\n".join(sections)

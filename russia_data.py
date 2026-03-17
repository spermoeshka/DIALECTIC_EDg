"""
russia_data.py v2.1 — Расширенные источники данных для РФ модуля.

ИСПРАВЛЕНО v2.1:
- fetch_urals_oil принимает wti_price из глобального анализа
  чтобы не было расхождения цен между Russia Edge и глобальным дайджестом.
  Urals = WTI - 3 (типичный дисконт Urals к WTI, не к Brent)
- fetch_russia_context принимает global_report для извлечения WTI цены

Источники:
1. ЦБ РФ — ключевая ставка, курсы валют
2. Мосбиржа MOEX ISS — IMOEX, акции, ОФЗ
3. РБК, Коммерсант, Ведомости, Интерфакс RSS
4. Росстат — инфляция РФ
5. Нефть Urals — синхронизирована с глобальным WTI
"""

import asyncio
import logging
import re
from datetime import datetime
import aiohttp

logger = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=15)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9",
}


# ─── 1. ЦБ РФ ─────────────────────────────────────────────────────────────────

async def fetch_cbr_data() -> str:
    results = []

    try:
        url = "https://www.cbr.ru/scripts/XML_daily.asp"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    text = await resp.text(encoding="windows-1251")
                    currencies = {
                        "USD": "💵 Доллар",
                        "EUR": "💶 Евро",
                        "CNY": "🇨🇳 Юань",
                        "TRY": "🇹🇷 Лира",
                        "AMD": "🇦🇲 Драм",
                        "KZT": "🇰🇿 Тенге",
                    }
                    for code, name in currencies.items():
                        pattern = rf'<CharCode>{code}</CharCode>.*?<Nominal>(\d+)</Nominal>.*?<Value>([\d,]+)</Value>'
                        m = re.search(pattern, text, re.DOTALL)
                        if m:
                            nominal = int(m.group(1))
                            val = float(m.group(2).replace(",", "."))
                            if nominal > 1:
                                results.append(f"• {name}: *{val:.2f} ₽* (за {nominal})")
                            else:
                                results.append(f"• {name}: *{val:.2f} ₽*")
    except Exception as e:
        logger.warning(f"CBR курсы error: {e}")

    try:
        url = "https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True&UniDbQuery.From=01.01.2025&UniDbQuery.To=31.12.2026"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    rates = re.findall(r'(\d{2}\.\d{2}\.\d{4})</td>\s*<td[^>]*>([\d,]+)', text)
                    if rates:
                        date, rate = rates[-1]
                        rate_val = rate.replace(",", ".")
                        rate_f = float(rate_val)
                        if rate_f >= 20:
                            comment = "🔴 _исторически высокая — давит на бизнес и ипотеку_"
                        elif rate_f >= 16:
                            comment = "🟠 _высокая — кредиты дорогие_"
                        else:
                            comment = "🟡 _умеренная_"
                        results.append(f"• 🏦 Ключевая ставка ЦБ: *{rate_val}%* {comment} _(на {date})_")
    except Exception as e:
        logger.warning(f"CBR ставка error: {e}")

    if not results:
        return ""

    lines = ["🏦 *ЦБ РФ — КУРСЫ И СТАВКА:*"] + results
    lines.append("_📌 Высокая ставка ЦБ = дорогие кредиты, давление на бизнес и рынок акций_")
    return "\n".join(lines)


# ─── 2. Мосбиржа ──────────────────────────────────────────────────────────────

async def fetch_moex_data() -> str:
    results = []

    async with aiohttp.ClientSession(headers=HEADERS) as session:

        # IMOEX индекс
        try:
            url = "https://iss.moex.com/iss/engines/stock/markets/index/securities/IMOEX.json"
            async with session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    cols = data["marketdata"]["columns"]
                    rows = data["marketdata"]["data"]
                    if rows:
                        d = dict(zip(cols, rows[0]))
                        last   = d.get("CURRENTVALUE") or d.get("LASTVALUE")
                        change = d.get("LASTCHANGEPRC") or 0
                        if last:
                            ch_emoji = "🟢" if change >= 0 else "🔴"
                            ch_str   = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"
                            results.append(f"• 📊 IMOEX: *{last:.2f}* {ch_emoji} {ch_str}")
        except Exception as e:
            logger.warning(f"MOEX IMOEX error: {e}")

        await asyncio.sleep(0.3)

        # Топ акции
        top_tickers = [
            ("SBER", "Сбер"), ("GAZP", "Газпром"), ("LKOH", "Лукойл"),
            ("YNDX", "Яндекс"), ("NVTK", "Новатэк"), ("ROSN", "Роснефть"),
            ("GMKN", "Норникель"), ("TCSG", "ТКС/Тинькофф"),
        ]

        stock_lines = []
        for ticker, name in top_tickers:
            try:
                url = f"https://iss.moex.com/iss/engines/stock/markets/shares/securities/{ticker}.json"
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        cols = data["marketdata"]["columns"]
                        rows = data["marketdata"]["data"]
                        if rows:
                            d      = dict(zip(cols, rows[0]))
                            price  = d.get("LAST") or d.get("WAPRICE")
                            change = d.get("LASTTOPREVPRICE") or 0
                            if price:
                                ch_emoji = "🟢" if change >= 0 else "🔴"
                                ch_str   = f"+{change:.1f}%" if change >= 0 else f"{change:.1f}%"
                                stock_lines.append(
                                    f"  {ticker} ({name}): *{price:.1f} ₽* {ch_emoji} {ch_str}"
                                )
                await asyncio.sleep(0.15)
            except Exception:
                continue

        if stock_lines:
            results.append("• 🏢 *Акции РФ:*\n" + "\n".join(stock_lines))

        # ОФЗ доходность
        try:
            url = "https://iss.moex.com/iss/engines/bond/markets/govt/securities.json?securities=SU26238RMFS4"
            async with session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    cols = data["marketdata"]["columns"]
                    rows = data["marketdata"]["data"]
                    if rows:
                        d         = dict(zip(cols, rows[0]))
                        yield_val = d.get("YIELD")
                        if yield_val:
                            results.append(
                                f"• 📜 ОФЗ доходность (10 лет): *{yield_val:.2f}%*\n"
                                f"  _📌 Если ОФЗ > ключевой ставки — рынок ждёт снижения ставки_"
                            )
        except Exception as e:
            logger.warning(f"MOEX OFZ error: {e}")

    if not results:
        return ""

    return "📈 *МОСБИРЖА:*\n" + "\n".join(results)


# ─── 3. Нефть Urals — ИСПРАВЛЕНО: синхронизация с глобальным WTI ─────────────

async def fetch_urals_oil(wti_price: float | None = None) -> str:
    """
    ИСПРАВЛЕНО v2.1: принимает wti_price из глобального анализа.
    Urals = WTI - 3$ (типичный дисконт, не к Brent).
    Это устраняет расхождение цен между Russia Edge и глобальным дайджестом.

    wti_price — цена WTI из web_search.py (тот же источник что и в глобальном анализе)
    """

    # Если передана цена WTI из глобального анализа — используем её
    if wti_price and wti_price > 30:
        urals = wti_price - 3   # Urals торгуется ~$3 дешевле WTI
        budget_price = 69.7     # цена заложенная в бюджет РФ 2025
        budget_status = "профицит" if urals > budget_price else "дефицит"
        diff = abs(urals - budget_price)
        budget_impact = diff * 1.5  # ~$1.5 трлн ₽ за каждые $10

        return (
            f"🛢️ *НЕФТЬ:*\n"
            f"• WTI (мировой рынок): *${wti_price:.1f}/баррель* "
            f"_(синхронизировано с глобальным анализом)_\n"
            f"• Urals (оценка): *~${urals:.1f}/баррель* (дисконт ~$3 к WTI)\n"
            f"• Бюджет РФ 2025 рассчитан при Urals $69.7 → "
            f"{'✅' if budget_status == 'профицит' else '⚠️'} {budget_status} "
            f"~${budget_impact:.0f} млрд/год\n"
            f"_📌 Каждые $10 изменения Urals = ~±1.5 трлн ₽ в бюджет РФ_"
        )

    # Fallback — собственный запрос к Yahoo за Brent
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/BZ=F"
        params = {"interval": "1d", "range": "2d"}
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, params=params, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    data  = await resp.json()
                    brent = data["chart"]["result"][0]["meta"].get("regularMarketPrice", 0)
                    if brent:
                        urals = brent - 12  # дисконт к Brent исторически больше
                        budget_status = "профицит" if urals > 69.7 else "дефицит"
                        return (
                            f"🛢️ *НЕФТЬ:*\n"
                            f"• Brent: *${brent:.1f}/баррель*\n"
                            f"• Urals (оценка): *~${urals:.1f}/баррель* (дисконт ~$12 к Brent)\n"
                            f"• Бюджет РФ 2025: Urals $69.7 → {budget_status} вероятен\n"
                            f"_📌 Каждые $10 изменения Urals = ~±1.5 трлн ₽ в бюджет РФ_"
                        )
    except Exception as e:
        logger.warning(f"Oil fallback error: {e}")

    return ""


# ─── 4. Новости РФ ────────────────────────────────────────────────────────────

async def fetch_russia_news() -> str:
    all_news = []

    rss_feeds = [
        ("РБК Экономика", "https://rss.rbc.ru/finances/rss.rss"),
        ("РБК Бизнес",    "https://rss.rbc.ru/business/rss.rss"),
        ("РБК Политика",  "https://rss.rbc.ru/politics/rss.rss"),
        ("Коммерсант",    "https://www.kommersant.ru/RSS/main.xml"),
        ("Ведомости",     "https://www.vedomosti.ru/rss/news"),
        ("Интерфакс",     "https://www.interfax.ru/rss.asp"),
    ]

    keywords = [
        "закон", "налог", "ставк", "цб", "рубл", "инфляц", "ввп",
        "бюджет", "дефицит", "профицит", "нефт", "газ", "экспорт",
        "бизнес", "предприниматель", "малый бизнес", "импорт", "льгот",
        "субсид", "кредит", "ипотек", "банкрот", "штраф",
        "санкц", "минфин", "минэконом", "госдума", "правительств",
        "указ", "постановлени", "регулир",
        "мосбирж", "акци", "облигац", "офз", "дивиденд",
        "строительств", "недвижимост", "логистик", "торговл",
    ]

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        for source_name, url in rss_feeds:
            try:
                async with session.get(url, timeout=TIMEOUT) as resp:
                    if resp.status == 200:
                        text   = await resp.text()
                        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', text)
                        if not titles:
                            titles = re.findall(r'<title>(.*?)</title>', text)

                        count = 0
                        for title in titles[1:15]:
                            title = title.strip()
                            title = re.sub(r'<[^>]+>', '', title)
                            if title and any(kw in title.lower() for kw in keywords):
                                all_news.append((source_name, title))
                                count += 1
                            if count >= 3:
                                break

                await asyncio.sleep(0.2)
            except Exception as e:
                logger.warning(f"RSS {source_name} error: {e}")
                continue

    if not all_news:
        return ""

    lines = ["📰 *НОВОСТИ РФ (РБК / Коммерсант / Ведомости / Интерфакс):*"]
    seen = set()
    for source, title in all_news[:10]:
        if title not in seen:
            seen.add(title)
            lines.append(f"• {title} _({source})_")

    return "\n".join(lines)


# ─── 5. Росстат — инфляция РФ ────────────────────────────────────────────────

async def fetch_rosstat_inflation() -> str:
    try:
        url = "https://rosstat.gov.ru/storage/mediabank/Ind_potreb_cen.htm"
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            async with session.get(url, timeout=TIMEOUT) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    pct  = re.findall(r'(\d+[.,]\d+)\s*%', text)
                    if pct:
                        val   = pct[0].replace(",", ".")
                        val_f = float(val)
                        gap   = val_f - 4.0
                        gap_str = f"+{gap:.1f}%" if gap > 0 else f"{gap:.1f}%"
                        status = "🔴 выше таргета ЦБ" if gap > 1 else "🟢 близко к таргету"
                        return (
                            f"📊 *ИНФЛЯЦИЯ РФ (Росстат):*\n"
                            f"• Инфляция РФ: *~{val}% годовых* {status}\n"
                            f"  _(таргет ЦБ РФ: 4%, отклонение: {gap_str})_\n"
                            f"_📌 Высокая инфляция РФ = ЦБ держит высокую ставку = дорогие кредиты_"
                        )
    except Exception as e:
        logger.warning(f"Rosstat error: {e}")
    return ""


# ─── 6. Извлечь WTI из глобального отчёта ────────────────────────────────────

def _extract_wti_from_report(global_report: str) -> float | None:
    """
    Извлекает цену WTI из глобального отчёта чтобы синхронизировать
    цену нефти в Russia Edge с глобальным дайджестом.
    """
    patterns = [
        r"Нефть WTI[^$]*\$([\d.]+)",
        r"WTI[^$]*\$([\d.]+)",
        r"OIL_WTI[^$]*\$([\d.]+)",
        r"CL=F[^$]*\$([\d.]+)",
        r"\$(\d{2,3}\.\d{1,2}).*?(?:барр|barrel|WTI|нефть)",
    ]
    for pattern in patterns:
        m = re.search(pattern, global_report, re.IGNORECASE)
        if m:
            price = float(m.group(1))
            if 30 <= price <= 250:  # санити чек
                logger.info(f"🛢️ WTI из глобального отчёта: ${price}")
                return price
    return None


# ─── 7. Главная функция ───────────────────────────────────────────────────────

async def fetch_russia_context(global_report: str = "") -> str:
    """
    Собирает все РФ данные параллельно.
    global_report — передаётся для синхронизации цены нефти.
    """
    logger.info("🇷🇺 Собираю расширенный контекст РФ...")

    # Извлекаем WTI из глобального анализа для синхронизации
    wti_price = _extract_wti_from_report(global_report) if global_report else None
    if wti_price:
        logger.info(f"🛢️ Синхронизирую нефть с глобальным анализом: WTI ${wti_price}")
    else:
        logger.info("🛢️ WTI из глобального анализа не найден — используем собственный запрос")

    results = await asyncio.gather(
        fetch_cbr_data(),
        fetch_moex_data(),
        fetch_urals_oil(wti_price=wti_price),  # передаём WTI
        fetch_rosstat_inflation(),
        fetch_russia_news(),
        return_exceptions=True
    )

    sections = []
    labels   = ["ЦБ РФ", "Мосбиржа", "Нефть Urals", "Инфляция РФ", "Новости РФ"]
    for label, result in zip(labels, results):
        if isinstance(result, str) and result.strip():
            sections.append(result)
        elif isinstance(result, Exception):
            logger.warning(f"Russia {label} error: {result}")

    if not sections:
        return "Данные по РФ временно недоступны."

    now    = datetime.now().strftime("%d.%m.%Y %H:%M")
    header = f"=== КОНТЕКСТ РФ ({now}) ===\n"
    return header + "\n\n".join(sections)

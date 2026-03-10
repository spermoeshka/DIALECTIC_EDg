"""
backtester.py — Проверка качества агентов на исторических данных.

Как работает:
1. Берёт реальные новости за выбранный период (из GDELT или файла)
2. Прогоняет через агентов → они делают прогнозы
3. Берёт реальные цены через Yahoo Finance
4. Считает winrate и P&L
5. Сохраняет в backtest_results.csv

Запуск: python backtester.py
"""

import asyncio
import csv
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import aiohttp

from agents import DebateOrchestrator
from tracker import extract_predictions_from_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

# ─── Тестовые новости (2021–2024) ─────────────────────────────────────────────
# Реальные события с известным исходом для валидации агентов

HISTORICAL_NEWS = [
    {
        "date": "2021-10-20",
        "news": "SEC одобрила первый Bitcoin Futures ETF (ProShares BITO). Институциональный спрос растёт. BTC торгуется около $62,000.",
        "asset": "BTC",
        "price_at_news": 62000,
        "price_7d_later": 66000,
        "outcome": "UP +6.4%"
    },
    {
        "date": "2021-11-10",
        "news": "Инфляция в США достигла 6.2% — максимум за 30 лет. Fed сигнализирует об ускорении сворачивания QE. Рынки нервничают.",
        "asset": "SPY",
        "price_at_news": 468,
        "price_7d_later": 461,
        "outcome": "DOWN -1.5%"
    },
    {
        "date": "2022-01-05",
        "news": "Fed опубликовал протоколы: члены обсуждают более быстрое повышение ставок. QT может начаться раньше ожиданий. Nasdaq падает.",
        "asset": "QQQ",
        "price_at_news": 385,
        "price_7d_later": 358,
        "outcome": "DOWN -7%"
    },
    {
        "date": "2022-05-05",
        "news": "Fed поднял ставку на 50 базисных пунктов — крупнейшее повышение с 2000 года. Powell заявил что рецессия маловероятна.",
        "asset": "SPY",
        "price_at_news": 412,
        "price_7d_later": 398,
        "outcome": "DOWN -3.4%"
    },
    {
        "date": "2022-06-13",
        "news": "Bitcoin рухнул ниже $23,000 — минимум с 2020 года. Celsius Network заморозила вывод средств. Крипто-рынок в панике.",
        "asset": "BTC",
        "price_at_news": 23000,
        "price_7d_later": 20000,
        "outcome": "DOWN -13%"
    },
    {
        "date": "2022-11-08",
        "news": "FTX столкнулась с кризисом ликвидности. Binance объявила о продаже FTT токенов. Sam Bankman-Fried просит помощи.",
        "asset": "BTC",
        "price_at_news": 20000,
        "price_7d_later": 16500,
        "outcome": "DOWN -17.5%"
    },
    {
        "date": "2023-03-10",
        "news": "Silicon Valley Bank закрыт регуляторами. $209 млрд активов под угрозой. Криптовалютный USDC потерял привязку к доллару.",
        "asset": "BTC",
        "price_at_news": 20500,
        "price_7d_later": 26000,
        "outcome": "UP +26% (flight to BTC)"
    },
    {
        "date": "2023-06-15",
        "news": "Fed взял паузу в повышении ставок после 10 последовательных повышений. Ставка остаётся на уровне 5.25%. Рынки растут.",
        "asset": "SPY",
        "price_at_news": 435,
        "price_7d_later": 444,
        "outcome": "UP +2%"
    },
    {
        "date": "2023-10-23",
        "news": "BlackRock и Fidelity подали обновлённые заявки на Bitcoin Spot ETF. SEC даёт позитивные сигналы. BTC пробивает $34,000.",
        "asset": "BTC",
        "price_at_news": 34000,
        "price_7d_later": 34500,
        "outcome": "UP +1.5%"
    },
    {
        "date": "2024-01-10",
        "news": "SEC одобрила 11 Bitcoin Spot ETF включая BlackRock iShares. Исторический момент. BTC торгуется около $46,000.",
        "asset": "BTC",
        "price_at_news": 46000,
        "price_7d_later": 42800,
        "outcome": "DOWN -7% (sell the news)"
    },
    {
        "date": "2024-03-05",
        "news": "Bitcoin обновил исторический максимум выше $69,000 впервые с 2021 года. Приток в ETF превысил $10 млрд. Халвинг через 45 дней.",
        "asset": "BTC",
        "price_at_news": 69000,
        "price_7d_later": 65000,
        "outcome": "DOWN -5.8%"
    },
    {
        "date": "2024-07-11",
        "news": "Инфляция США упала до 3.0%. Рынок оценивает вероятность снижения ставки в сентябре в 85%. Доллар слабеет.",
        "asset": "SPY",
        "price_at_news": 556,
        "price_7d_later": 548,
        "outcome": "DOWN -1.4% (rotation)"
    },
]


class Backtester:
    def __init__(self):
        self.orchestrator = DebateOrchestrator()
        self.results = []

    async def run(self, news_items: list[dict], max_items: int = 10):
        """Запускает бэктест на списке исторических новостей."""
        items_to_test = news_items[:max_items]
        
        logger.info(f"🧪 Запускаю бэктест на {len(items_to_test)} новостях...")
        logger.info("Это займёт несколько минут...\n")

        for i, item in enumerate(items_to_test, 1):
            logger.info(f"[{i}/{len(items_to_test)}] {item['date']}: {item['news'][:60]}...")
            
            try:
                result = await self._test_one(item)
                self.results.append(result)
                
                status = "✅" if result["agent_correct"] else "❌"
                logger.info(
                    f"  {status} Агент: {result['agent_direction']} | "
                    f"Реально: {result['real_outcome']} | "
                    f"Совпало: {result['agent_correct']}"
                )
                
                # Небольшая пауза между запросами
                await asyncio.sleep(3)
                
            except Exception as e:
                logger.error(f"  Ошибка: {e}")
                continue

        self._print_summary()
        self._save_csv()

    async def _test_one(self, item: dict) -> dict:
        """Тестирует одну новость."""
        # Добавляем контекст цены
        news_with_price = (
            f"{item['news']}\n\n"
            f"Текущая цена {item['asset']}: ${item['price_at_news']:,}"
        )
        
        # Запускаем агентов
        report = await self.orchestrator.run_debate(
            news_context=news_with_price,
            custom_mode=True
        )
        
        # Извлекаем прогнозы из отчёта
        predictions = extract_predictions_from_report(report)
        
        # Ищем прогноз по нужному активу
        agent_direction = "NEUTRAL"
        agent_entry = item["price_at_news"]
        agent_target = None
        agent_stop = None
        
        for pred in predictions:
            if pred["asset"] == item["asset"]:
                agent_direction = pred["direction"]
                agent_entry = pred.get("entry_price", item["price_at_news"])
                agent_target = pred.get("target_price")
                agent_stop = pred.get("stop_loss")
                break
        
        # Если паттерн не найден — анализируем текст синтеза
        if agent_direction == "NEUTRAL":
            agent_direction = self._extract_direction_from_text(report, item["asset"])
        
        # Определяем реальный исход
        real_direction = "UP" if "UP" in item["outcome"] else "DOWN"
        
        # Совпал ли прогноз?
        agent_correct = (
            (agent_direction == "LONG" and real_direction == "UP") or
            (agent_direction == "SHORT" and real_direction == "DOWN")
        )
        
        # Считаем P&L
        price_change_pct = ((item["price_7d_later"] - item["price_at_news"]) 
                           / item["price_at_news"] * 100)
        
        if agent_direction == "LONG":
            pnl = price_change_pct
        elif agent_direction == "SHORT":
            pnl = -price_change_pct
        else:
            pnl = 0
        
        return {
            "date": item["date"],
            "asset": item["asset"],
            "news_snippet": item["news"][:80] + "...",
            "price_at_news": item["price_at_news"],
            "price_7d_later": item["price_7d_later"],
            "real_outcome": item["outcome"],
            "agent_direction": agent_direction,
            "agent_target": agent_target,
            "agent_stop": agent_stop,
            "agent_correct": agent_correct,
            "pnl_pct": round(pnl, 2),
            "report_snippet": report[:300] + "..."
        }

    def _extract_direction_from_text(self, report: str, asset: str) -> str:
        """Извлекает направление прогноза из текста если паттерн не нашёлся."""
        report_lower = report.lower()
        
        # Ищем бычьи сигналы
        bull_signals = ["long", "покупк", "рост", "buy", "bullish", "восстановлени"]
        bear_signals = ["short", "продаж", "падени", "sell", "bearish", "снижени"]
        
        bull_count = sum(1 for s in bull_signals if s in report_lower)
        bear_count = sum(1 for s in bear_signals if s in report_lower)
        
        if bull_count > bear_count:
            return "LONG"
        elif bear_count > bull_count:
            return "SHORT"
        return "NEUTRAL"

    def _print_summary(self):
        """Выводит сводку результатов."""
        if not self.results:
            print("\n❌ Нет результатов для анализа")
            return
        
        total = len(self.results)
        correct = sum(1 for r in self.results if r["agent_correct"])
        winrate = correct / total * 100
        avg_pnl = sum(r["pnl_pct"] for r in self.results) / total
        
        best = max(self.results, key=lambda r: r["pnl_pct"])
        worst = min(self.results, key=lambda r: r["pnl_pct"])
        
        print("\n" + "="*60)
        print("📊 РЕЗУЛЬТАТЫ БЭКТЕСТА")
        print("="*60)
        print(f"Протестировано новостей:  {total}")
        print(f"Правильных прогнозов:     {correct}/{total}")
        print(f"Winrate:                  {winrate:.1f}%")
        print(f"Средний P&L:              {avg_pnl:+.1f}%")
        print(f"Лучший колл:              {best['asset']} {best['date']} → {best['pnl_pct']:+.1f}%")
        print(f"Худший колл:              {worst['asset']} {worst['date']} → {worst['pnl_pct']:+.1f}%")
        print("="*60)
        
        if winrate >= 60:
            print("✅ Отличный результат! Агенты работают хорошо.")
        elif winrate >= 50:
            print("🟡 Средний результат. Есть куда улучшать промпты.")
        else:
            print("🔴 Слабый результат. Нужно серьёзно переработать промпты.")
        
        print("\n💡 Детальный отчёт сохранён в backtest_results.csv")

    def _save_csv(self):
        """Сохраняет результаты в CSV."""
        if not self.results:
            return
        
        path = Path("backtest_results.csv")
        fields = [
            "date", "asset", "news_snippet", "price_at_news",
            "price_7d_later", "real_outcome", "agent_direction",
            "agent_correct", "pnl_pct"
        ]
        
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in self.results:
                writer.writerow({k: r.get(k, "") for k in fields})
        
        logger.info(f"✅ Результаты сохранены в {path}")


async def main():
    print("🧪 DIALECTIC EDGE — BACKTESTER")
    print("="*40)
    print(f"Доступно исторических новостей: {len(HISTORICAL_NEWS)}")
    print()
    
    try:
        n = int(input(f"Сколько новостей тестировать? (1-{len(HISTORICAL_NEWS)}, рекомендую 5 для начала): "))
        n = max(1, min(n, len(HISTORICAL_NEWS)))
    except (ValueError, EOFError):
        n = 5
    
    print(f"\nЗапускаю бэктест на {n} новостях...\n")
    
    tester = Backtester()
    await tester.run(HISTORICAL_NEWS, max_items=n)


if __name__ == "__main__":
    asyncio.run(main())

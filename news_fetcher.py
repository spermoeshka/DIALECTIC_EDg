"""
news_fetcher.py — Сбор новостей через RSS и опционально NewsAPI.
Полностью бесплатный (RSS не требует ключей).
"""

import asyncio
import logging
import re
from datetime import datetime
from typing import Optional

import aiohttp
import feedparser

from config import RSS_FEEDS, MAX_NEWS_PER_FEED, MAX_TOTAL_NEWS, NEWS_API_KEY

logger = logging.getLogger(__name__)


class NewsItem:
    def __init__(self, title: str, summary: str, source: str, link: str = "", published: str = ""):
        self.title = title.strip()
        self.summary = self._clean(summary)[:400]  # ограничиваем длину
        self.source = source
        self.link = link
        self.published = published

    @staticmethod
    def _clean(text: str) -> str:
        """Удаляет HTML-теги и лишние пробелы."""
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def to_text(self) -> str:
        return f"[{self.source}] {self.title}. {self.summary}"

    def to_formatted(self) -> str:
        return f"• *{self.title}*\n  _{self.source}_ | {self.published}"


class NewsFetcher:
    """Асинхронный сборщик новостей из нескольких источников."""

    async def fetch_all(self) -> str:
        """Собирает все новости и возвращает единый текстовый контекст."""
        tasks = []

        # RSS ленты
        for name, url in RSS_FEEDS.items():
            tasks.append(self._fetch_rss(name, url))

        # NewsAPI (если ключ есть)
        if NEWS_API_KEY:
            tasks.append(self._fetch_newsapi())

        # Запускаем параллельно с таймаутом
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_items: list[NewsItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning(f"Feed error (ignored): {result}")
                continue
            if isinstance(result, list):
                all_items.extend(result)

        # Дедупликация по заголовку
        seen_titles = set()
        unique_items = []
        for item in all_items:
            key = item.title.lower()[:60]
            if key not in seen_titles:
                seen_titles.add(key)
                unique_items.append(item)

        # Ограничение общего количества
        unique_items = unique_items[:MAX_TOTAL_NEWS]

        if not unique_items:
            return "Новости не удалось загрузить. Используй /analyze [текст новости] для ручного ввода."

        return self._build_context(unique_items)

    async def _fetch_rss(self, source_name: str, url: str) -> list[NewsItem]:
        """Загружает RSS-ленту через feedparser (через aiohttp для async)."""
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            headers = {"User-Agent": "Mozilla/5.0 (compatible; DialecticEdgeBot/1.0)"}
            
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url, timeout=timeout) as resp:
                    content = await resp.text()

            feed = feedparser.parse(content)
            items = []

            for entry in feed.entries[:MAX_NEWS_PER_FEED]:
                title = entry.get("title", "")
                summary = entry.get("summary", entry.get("description", ""))
                link = entry.get("link", "")
                published = entry.get("published", "")

                if title:
                    items.append(NewsItem(
                        title=title,
                        summary=summary,
                        source=source_name,
                        link=link,
                        published=published[:16] if published else ""
                    ))

            logger.info(f"RSS {source_name}: получено {len(items)} новостей")
            return items

        except Exception as e:
            logger.warning(f"RSS {source_name} ({url}): {e}")
            return []

    async def _fetch_newsapi(self) -> list[NewsItem]:
        """Загружает топ-новости через NewsAPI.org (требует бесплатный ключ)."""
        try:
            url = "https://newsapi.org/v2/top-headlines"
            params = {
                "apiKey": NEWS_API_KEY,
                "category": "business",
                "language": "en",
                "pageSize": MAX_NEWS_PER_FEED,
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    data = await resp.json()

            items = []
            for article in data.get("articles", []):
                title = article.get("title", "")
                description = article.get("description", "")
                source = article.get("source", {}).get("name", "NewsAPI")

                if title and "[Removed]" not in title:
                    items.append(NewsItem(
                        title=title,
                        summary=description or "",
                        source=source,
                        link=article.get("url", "")
                    ))

            logger.info(f"NewsAPI: получено {len(items)} новостей")
            return items

        except Exception as e:
            logger.warning(f"NewsAPI error: {e}")
            return []

    def _build_context(self, items: list[NewsItem]) -> str:
        """Формирует единый текстовый контекст для агентов."""
        now = datetime.now().strftime("%d %B %Y, %H:%M UTC")
        
        lines = [
            f"=== АКТУАЛЬНЫЕ НОВОСТИ ({now}) ===",
            f"Источников: {len(set(i.source for i in items))} | Новостей: {len(items)}",
            "",
        ]

        # Группировка по источнику
        by_source: dict[str, list[NewsItem]] = {}
        for item in items:
            by_source.setdefault(item.source, []).append(item)

        for source, source_items in by_source.items():
            lines.append(f"--- {source} ---")
            for item in source_items:
                lines.append(f"• {item.title}")
                if item.summary:
                    lines.append(f"  {item.summary[:200]}")
            lines.append("")

        return "\n".join(lines)

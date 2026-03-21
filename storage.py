"""
storage.py — Простое кэширование отчётов в JSON-файл.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from config import CACHE_FILE, CACHE_TTL_HOURS

logger = logging.getLogger(__name__)


class Storage:
    def __init__(self):
        self.cache_path = Path(CACHE_FILE)
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Cache load error: {e}")
        return {}

    def _save(self):
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Cache save error: {e}")

    def cache_report(self, report: str, prices: dict | None = None):
        """Кэширует последний отчёт и снимок цен (для графика при отдаче из кэша)."""
        entry = {
            "report": report,
            "timestamp": datetime.now().strftime("%d.%m.%Y %H:%M"),
            "expires": (datetime.now() + timedelta(hours=CACHE_TTL_HOURS)).isoformat(),
        }
        if prices is not None:
            entry["prices"] = prices
        self._data["last_report"] = entry
        self._save()
        logger.info("Отчёт кэширован")

    def get_cached_report(self) -> dict | None:
        """Возвращает кэшированный отчёт, если он ещё актуален."""
        cached = self._data.get("last_report")
        if not cached:
            return None
        
        expires = datetime.fromisoformat(cached.get("expires", "2000-01-01"))
        if datetime.now() > expires:
            logger.info("Кэш устарел")
            return None
        
        return cached

    def clear_cache(self):
        self._data = {}
        self._save()

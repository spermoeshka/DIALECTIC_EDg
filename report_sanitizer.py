"""
Жёсткий пост-фильтр ответов агентов: вырезает строки с типовыми галлюцинациями,
которые промпт не всегда удерживает (история, «халвинг ETH», EIP и т.д.).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Строка удаляется целиком, если совпадает любой паттерн
_LINE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"исторически\b", re.IGNORECASE),
    re.compile(r"историческ(ая|ий|ое|ие)\s+(точк|сигнал|аналог|уровн)", re.IGNORECASE),
    re.compile(r"\bв\s+20(1\d|2[0-3])\s+году\b", re.IGNORECASE),
    re.compile(r"март[еу]?\s+2020", re.IGNORECASE),
    re.compile(r"в\s+марте\s+2020", re.IGNORECASE),
    re.compile(r"как\s+в\s+20\d{2}", re.IGNORECASE),
    re.compile(r"аналогично\s+прошл", re.IGNORECASE),
    re.compile(r"средн(ее|ий|ая)\s+за\s+последние\s+\d+\s+лет", re.IGNORECASE),
    re.compile(r"VIX\s+достиг.*\b80\b", re.IGNORECASE),
    re.compile(r"\b80\+\b.*VIX|VIX.*\b80\+", re.IGNORECASE),
    re.compile(r"халвинг.*(ethereum|eth|эфир|эфира)", re.IGNORECASE),
    re.compile(r"(ethereum|eth|эфир).{0,40}халвинг", re.IGNORECASE),
    re.compile(r"EIP-4844", re.IGNORECASE),
    re.compile(r"Dencun|Прото-данкшард", re.IGNORECASE),
    re.compile(r"Extreme\s+Fear.{0,80}точк[ауы]\s+входа", re.IGNORECASE),
    re.compile(r"Fear\s*&\s*Greed.{0,120}историческ", re.IGNORECASE),
    re.compile(r"12/100.{0,80}историческ", re.IGNORECASE),
    re.compile(r"точк[ауы]\s+входа.{0,40}историческ", re.IGNORECASE),
    re.compile(r"история\s+(показывает|учит|доказывает)", re.IGNORECASE),
    re.compile(r"как\s+показывает\s+практика", re.IGNORECASE),
    re.compile(r"практика\s+показывает", re.IGNORECASE),
]


def sanitize_agent_output(text: str) -> tuple[str, int]:
    """Возвращает очищенный текст и число удалённых строк."""
    if not text or not text.strip():
        return text, 0
    lines = text.split("\n")
    kept: list[str] = []
    removed = 0
    for line in lines:
        if any(p.search(line) for p in _LINE_PATTERNS):
            removed += 1
            continue
        kept.append(line)
    out = "\n".join(kept)
    if removed:
        logger.info("report_sanitizer: удалено %s строк(и)", removed)
    return out, removed


def sanitize_full_report(text: str) -> tuple[str, int]:
    """Тот же фильтр для целого отчёта (страховка перед кэшем)."""
    return sanitize_agent_output(text)

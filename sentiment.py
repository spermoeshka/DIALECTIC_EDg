"""
sentiment.py — Sentiment scoring и селективный фильтр уверенности.

Два улучшения качества анализа:

1. SENTIMENT SCORING (идея Gemini)
   Прогоняет сырые новости через лёгкий анализ ДО агентов.
   Агенты получают готовый score (-1 до +1), а не стены текста.
   Решает проблему "Lost in the middle" у Flash.

2. CONFIDENCE FILTER (идея из дебатов)
   Если сигнал слабый — Synth не выдаёт торговую рекомендацию.
   Только анализ. Это поднимает реальный winrate на сильных сигналах.
"""

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─── Sentiment Scoring ────────────────────────────────────────────────────────

# Позитивные финансовые сигналы
BULL_WORDS = [
    "рост", "растёт", "вырос", "повысился", "прибыль", "прорыв", "максимум",
    "одобрен", "одобрила", "купил", "покупка", "инвестиции", "партнёрство",
    "bullish", "surge", "rally", "high", "growth", "profit", "approved",
    "buy", "long", "upgrade", "beat", "exceeded", "record", "adoption",
    "халвинг", "etf", "институциональный", "снизил ставку", "смягчение"
]

# Негативные финансовые сигналы
BEAR_WORDS = [
    "падение", "упал", "снизился", "убыток", "банкротство", "запрет",
    "санкции", "арест", "взлом", "кризис", "инфляция", "рецессия",
    "bearish", "crash", "dump", "ban", "hack", "fraud", "loss", "sell",
    "short", "downgrade", "missed", "warning", "fear", "panic", "liquidation",
    "повысил ставку", "ужесточение", "регуляция", "обвал", "коллапс"
]

# Нейтральные/неопределённые сигналы
NEUTRAL_WORDS = [
    "ожидает", "возможно", "вероятно", "неопределённость", "смешанный",
    "может", "если", "perhaps", "uncertain", "mixed", "wait", "hold"
]


@dataclass
class SentimentResult:
    score: float          # от -1.0 (медвежий) до +1.0 (бычий)
    label: str            # BULLISH / BEARISH / NEUTRAL / MIXED
    confidence: str       # HIGH / MEDIUM / LOW
    bull_signals: int
    bear_signals: int
    summary: str          # текст для агентов


def score_text(text: str) -> SentimentResult:
    """
    Быстрый подсчёт sentiment score по ключевым словам.
    Не ML — но работает достаточно хорошо для фильтрации.
    """
    text_lower = text.lower()

    bull_count = sum(1 for w in BULL_WORDS if w in text_lower)
    bear_count = sum(1 for w in BEAR_WORDS if w in text_lower)
    neutral_count = sum(1 for w in NEUTRAL_WORDS if w in text_lower)

    total = bull_count + bear_count
    if total == 0:
        score = 0.0
    else:
        score = (bull_count - bear_count) / total

    # Метка
    if score > 0.3:
        label = "BULLISH"
    elif score < -0.3:
        label = "BEARISH"
    elif neutral_count > 3:
        label = "NEUTRAL"
    else:
        label = "MIXED"

    # Уверенность — насколько сигнал однозначный
    imbalance = abs(bull_count - bear_count)
    if imbalance >= 5 and total >= 8:
        confidence = "HIGH"
    elif imbalance >= 3 and total >= 4:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Итоговое резюме для агентов
    bar_bull = "█" * min(bull_count, 10)
    bar_bear = "█" * min(bear_count, 10)

    summary = (
        f"📊 SENTIMENT SCORE: {score:+.2f} → {label}\n"
        f"Уверенность сигнала: {confidence}\n"
        f"🐂 Бычьих сигналов: {bull_count} {bar_bull}\n"
        f"🐻 Медвежьих сигналов: {bear_count} {bar_bear}\n"
        f"❓ Неопределённость: {neutral_count}\n"
    )

    if confidence == "HIGH":
        summary += f"✅ Сигнал чёткий — агентам рекомендуется учитывать {label} направление.\n"
    elif confidence == "MEDIUM":
        summary += "⚠️ Сигнал умеренный — анализировать внимательно.\n"
    else:
        summary += "❌ Сигнал слабый — высокая неопределённость, торговые рекомендации нежелательны.\n"

    return SentimentResult(
        score=score,
        label=label,
        confidence=confidence,
        bull_signals=bull_count,
        bear_signals=bear_count,
        summary=summary
    )


# ─── Confidence Filter ────────────────────────────────────────────────────────

# Инструкция для Synth в зависимости от уверенности
CONFIDENCE_INSTRUCTIONS = {
    "HIGH": """
🟢 РЕЖИМ: СИЛЬНЫЙ СИГНАЛ
Sentiment score показывает чёткое направление с высокой уверенностью.
Можешь давать конкретные торговые рекомендации с точками входа и стопами.
Но всё равно честно — если агенты расходятся, скажи об этом.
""",

    "MEDIUM": """
🟡 РЕЖИМ: УМЕРЕННЫЙ СИГНАЛ
Sentiment неоднозначный. Можешь давать рекомендации но только с пометкой
"умеренный сигнал — маленькая позиция или жди подтверждения".
Акцент на сценариях и рисках, не на конкретных точках входа.
""",

    "LOW": """
🔴 РЕЖИМ: СЛАБЫЙ СИГНАЛ — НЕ ДАВАЙ ТОРГОВЫЕ РЕКОМЕНДАЦИИ
Данные противоречивы или их недостаточно для уверенного прогноза.
ЗАПРЕЩЕНО давать торговые рекомендации (LONG/SHORT/вход/стоп).
Вместо этого:
- Дай качественный анализ ситуации
- Опиши что нужно отслеживать
- Честно скажи: "Сигнал слабый — лучше подождать ясности"
- Это ЧЕСТНЫЙ и ПРАВИЛЬНЫЙ ответ, не провал

Помни: пользователь который не потерял деньги на плохом сигнале —
доверяет боту больше чем тот кто потерял на "уверенном" прогнозе.
"""
}


def get_confidence_instruction(confidence: str) -> str:
    return CONFIDENCE_INSTRUCTIONS.get(confidence, CONFIDENCE_INSTRUCTIONS["LOW"])


# ─── Главная функция ──────────────────────────────────────────────────────────

def analyze_and_filter(news_text: str, market_data: str = "") -> tuple[SentimentResult, str]:
    """
    Анализирует входящий контекст и возвращает:
    1. SentimentResult — результат анализа
    2. str — инструкция для Synth агента

    Использование в main.py:
        sentiment, instruction = analyze_and_filter(news_context)
        # Добавь instruction в контекст Synth
    """
    combined = news_text + " " + market_data
    result = score_text(combined)
    instruction = get_confidence_instruction(result.confidence)

    logger.info(
        f"Sentiment: {result.label} ({result.score:+.2f}) | "
        f"Confidence: {result.confidence} | "
        f"Bull: {result.bull_signals} Bear: {result.bear_signals}"
    )

    return result, instruction


def format_for_agents(result: SentimentResult, instruction: str) -> str:
    """Форматирует всё для передачи агентам."""
    return (
        f"\n{'='*50}\n"
        f"{result.summary}\n"
        f"{instruction}"
        f"{'='*50}\n"
    )

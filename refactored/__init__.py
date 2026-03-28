"""
Refactored Dialectic Edge v7.1

Структура:
- main.py — точка входа
- handlers/ — обработчики команд Telegram
- keyboards.py — клавиатуры Inline/Reply
- services.py — бизнес-логика
- utils.py — утилиты
- state.py — глобальное состояние

Использование:
    python -m refactored.main

Или заменить оригинальный main.py рефакторенным.
"""

from .services import run_full_analysis, set_scheduler

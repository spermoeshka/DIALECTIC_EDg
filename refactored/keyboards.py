"""
Клавиатуры для бота.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def feedback_keyboard(report_type: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Полезно", callback_data=f"fb:1:{report_type}"),
        InlineKeyboardButton(text="👎 Мимо",    callback_data=f"fb:-1:{report_type}"),
    ]])


def debates_keyboard(user_id: int, round_idx: int, total_rounds: int) -> InlineKeyboardMarkup:
    buttons = []

    nav_row = []
    if round_idx > 0:
        nav_row.append(InlineKeyboardButton(
            text="◀️ Назад",
            callback_data=f"debate:{user_id}:{round_idx - 1}"
        ))
    nav_row.append(InlineKeyboardButton(
        text=f"📄 {round_idx + 1}/{total_rounds}",
        callback_data="debate:noop"
    ))
    if round_idx < total_rounds - 1:
        nav_row.append(InlineKeyboardButton(
            text="Вперёд ▶️",
            callback_data=f"debate:{user_id}:{round_idx + 1}"
        ))

    buttons.append(nav_row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def main_report_keyboard(user_id: int, has_debates: bool = True) -> InlineKeyboardMarkup:
    buttons = []
    if has_debates:
        buttons.append([
            InlineKeyboardButton(
                text="📖 Полные дебаты агентов",
                callback_data=f"debate:{user_id}:0"
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="👍 Полезно", callback_data="fb:1:daily"),
        InlineKeyboardButton(text="👎 Мимо",    callback_data="fb:-1:daily"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def profile_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🛡️ Консерватор", callback_data="profile:risk:conservative"),
            InlineKeyboardButton(text="⚖️ Умеренный",   callback_data="profile:risk:moderate"),
            InlineKeyboardButton(text="🚀 Агрессивный", callback_data="profile:risk:aggressive"),
        ],
        [
            InlineKeyboardButton(text="⚡ Скальпинг", callback_data="profile:hz:scalp"),
            InlineKeyboardButton(text="📈 Свинг",     callback_data="profile:hz:swing"),
            InlineKeyboardButton(text="💎 Инвест",    callback_data="profile:hz:invest"),
        ],
        [
            InlineKeyboardButton(text="₿ Крипта",    callback_data="profile:mkt:crypto"),
            InlineKeyboardButton(text="📈 Акции",     callback_data="profile:mkt:stocks"),
            InlineKeyboardButton(text="🌍 Всё",       callback_data="profile:mkt:all"),
        ],
    ])


def russia_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="✅ Сначала запущу /daily",
            callback_data="russia_choice:daily"
        ),
        InlineKeyboardButton(
            text="🚀 Запустить сейчас",
            callback_data="russia_choice:now"
        ),
    ]])

"""
config.py — Центральная конфигурация Dialectic Edge.
Все настройки меняются здесь.
"""

import os

# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
# Получить токен: https://t.me/BotFather → /newbot
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")

# ID администраторов (можно узнать через @userinfobot)
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "0").split(",") if x.strip().isdigit()]

# ─── AI ПРОВАЙДЕР ─────────────────────────────────────────────────────────────
# Варианты: "gemini" | "openai_compatible" | "ollama" (не рекомендуется)
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini")

# --- Ollama (локально, бесплатно) ---
# Установка: https://ollama.ai → ollama pull llama3.2
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")  # или qwen2.5, gemma2

# --- Google Gemini (бесплатный tier) ---
# Получить ключ: https://aistudio.google.com/app/apikey
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")  # бесплатный

# --- OpenAI-совместимый (LM Studio, Together.ai, Groq и т.д.) ---
OPENAI_COMPAT_BASE_URL = os.getenv("OPENAI_COMPAT_BASE_URL", "http://localhost:1234/v1")
OPENAI_COMPAT_API_KEY = os.getenv("OPENAI_COMPAT_API_KEY", "lm-studio")
OPENAI_COMPAT_MODEL = os.getenv("OPENAI_COMPAT_MODEL", "local-model")

# ─── ДЕБАТЫ ───────────────────────────────────────────────────────────────────
DEBATE_ROUNDS = int(os.getenv("DEBATE_ROUNDS", "3"))      # 3–5 раундов
MAX_TOKENS_PER_AGENT = int(os.getenv("MAX_TOKENS", "1500")) # токенов на ответ агента
AGENT_TEMPERATURE = float(os.getenv("AGENT_TEMP", "0.7"))

# ─── НОВОСТИ ──────────────────────────────────────────────────────────────────
# NewsAPI (опционально, бесплатный ключ: https://newsapi.org)
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")

# RSS ленты (бесплатно, без ключей)
RSS_FEEDS = {
    "Reuters Markets":    "https://feeds.reuters.com/reuters/businessNews",
    "Reuters World":      "https://feeds.reuters.com/Reuters/worldNews",
    "CNBC Markets":       "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135",
    "CoinDesk":           "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Investing.com Eco":  "https://www.investing.com/rss/news_14.rss",
    "FT Markets":         "https://www.ft.com/rss/home/uk",
    "Yahoo Finance":      "https://finance.yahoo.com/news/rssindex",
    "Cointelegraph":      "https://cointelegraph.com/rss",
}

MAX_NEWS_PER_FEED = int(os.getenv("MAX_NEWS_PER_FEED", "3"))   # статей с каждой ленты
MAX_TOTAL_NEWS = int(os.getenv("MAX_TOTAL_NEWS", "15"))         # лимит всего

# ─── ХРАНИЛИЩЕ ────────────────────────────────────────────────────────────────
CACHE_FILE = "cache.json"
CACHE_TTL_HOURS = int(os.getenv("CACHE_TTL_HOURS", "2"))  # кэш на 2 часа

# ─── ФОРМАТИРОВАНИЕ ───────────────────────────────────────────────────────────
DISCLAIMER = (
    "\n\n─────────────────────────\n"
    "🤝 *Честно о боте:*\n"
    "Это AI-анализ на основе публичных данных — не предсказание будущего.\n"
    "Рынок непредсказуем. Агенты могут ошибаться и иногда ошибаются.\n"
    "Где данных не хватало — агенты должны были это указать явно.\n"
    "Используй как один из инструментов мышления, не как сигнал к действию.\n\n"
    "⚠️ *Не является финансовым советом. DYOR. Торговля = риск потери капитала.*"
)

"""
ai_provider.py — Мультипровайдер с роутингом по агентам.

ИСПРАВЛЕНО v3:
- Bull/Verifier → Mistral Small (основной, не тратит Groq лимит)
- OpenRouter: Llama 3.3 70B (основной) → Gemma 3 27B (запасной)
- Добавлен MODELS_USED — трекинг какие модели участвовали в анализе
  Используется в agents.py для честного лейбла в отчёте
- Together убран (платный)
- Throttle 3 сек + Lock для параллельных вызовов

Агенты:
  🐂 Bull      → Mistral Small  → Groq → OpenRouter/Llama → OpenRouter/Gemma
  🐻 Bear      → Mistral Small  → Groq → OpenRouter/Llama → OpenRouter/Gemma
  🔍 Verifier  → Mistral Small  → Groq → OpenRouter/Llama → OpenRouter/Gemma
  ⚖️ Synth     → Mistral Large  → Mistral Small → OpenRouter/Llama → OpenRouter/Gemma
"""

import logging
import os
import time
import asyncio
import aiohttp

from config import MAX_TOKENS_PER_AGENT, AGENT_TEMPERATURE

logger = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=180)

# ── Ключи ──────────────────────────────────────────────────────────────────────
MISTRAL_API_KEY    = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_MODEL      = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_URL        = "https://api.mistral.ai/v1/chat/completions"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
GROQ_URL           = "https://api.groq.com/openai/v1/chat/completions"


# ── Трекинг моделей для честного лейбла в отчёте ─────────────────────────────
# agents.py читает это и вставляет в заголовок отчёта
MODELS_USED: dict[str, str] = {}  # {"bull": "Mistral Small", "synth": "Mistral Large", ...}

def _track_model(agent_key: str, provider: str, model: str):
    """Записывает какая модель отработала для каждого агента."""
    labels = {
        "mistral-small-latest":            "Mistral Small",
        "mistral-large-latest":            "Mistral Large",
        "llama-3.3-70b-versatile":         "Groq/Llama 3.3 70B",
        "meta-llama/llama-3.3-70b-instruct:free": "OpenRouter/Llama 3.3 70B",
        "google/gemma-3-27b-it:free":      "OpenRouter/Gemma 3 27B",
    }
    label = labels.get(model, f"{provider}/{model}")
    MODELS_USED[agent_key] = label
    logger.info(f"[{agent_key}] использует: {label}")


def get_models_summary() -> str:
    """Возвращает строку для заголовка отчёта."""
    if not MODELS_USED:
        return "🐂 Bull | 🐻 Bear | 🔍 Verifier | ⚖️ Synth"

    bull     = MODELS_USED.get("bull", "?")
    bear     = MODELS_USED.get("bear", "?")
    verifier = MODELS_USED.get("verifier", "?")
    synth    = MODELS_USED.get("synth", "?")

    return (
        f"🐂 Bull = {bull} | "
        f"🐻 Bear = {bear} | "
        f"🔍 Verifier = {verifier} | "
        f"⚖️ Synth = {synth}"
    )


# ── Модели по агентам ──────────────────────────────────────────────────────────
AGENT_MODELS = {
    "bull":     {"provider": "mistral", "model": "mistral-small-latest"},
    "verifier": {"provider": "mistral", "model": "mistral-small-latest"},
    "bear":     {"provider": "mistral", "model": "mistral-small-latest"},
    "synth":    {"provider": "mistral", "model": "mistral-large-latest"},
}

# Токены увеличены для лучшего анализа
_AGENT_MAX_TOKENS = {
    "bull":     1500,
    "bear":     1500,
    "verifier": 1000,
    "synth":    6000,
}


# ── Базовый вызов ──────────────────────────────────────────────────────────────

async def _call_openai_style(
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: str,
    temperature: float,
    name: str,
    extra_headers: dict = None,
    agent_key: str = None
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    max_tok = _AGENT_MAX_TOKENS.get(agent_key, MAX_TOKENS_PER_AGENT)

    payload = {
        "model": model,
        "messages": messages,
        "temperature": min(temperature, 1.0),
        "max_tokens": max_tok,
    }

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise RuntimeError(f"{name} HTTP {resp.status}: {err[:300]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()


# ── Конкретные провайдеры ──────────────────────────────────────────────────────

async def _call_groq(prompt: str, system: str, temperature: float, model: str = None, agent_key: str = None) -> str:
    if not GROQ_API_KEY:
        raise ValueError("Нет GROQ_API_KEY")
    m = model or "llama-3.3-70b-versatile"
    result = await _call_openai_style(
        GROQ_URL, GROQ_API_KEY, m,
        prompt, system, temperature, "Groq",
        agent_key=agent_key
    )
    if agent_key:
        _track_model(agent_key, "Groq", m)
    return result


async def _call_mistral(prompt: str, system: str, temperature: float, model: str = None, agent_key: str = None) -> str:
    if not MISTRAL_API_KEY:
        raise ValueError("Нет MISTRAL_API_KEY")
    m = model or MISTRAL_MODEL
    result = await _call_openai_style(
        MISTRAL_URL, MISTRAL_API_KEY, m,
        prompt, system, temperature, "Mistral",
        agent_key=agent_key
    )
    if agent_key:
        _track_model(agent_key, "Mistral", m)
    return result


async def _call_openrouter_llama(prompt: str, system: str, temperature: float, agent_key: str = None) -> str:
    """OpenRouter / Llama 3.3 70B — основной бесплатный fallback."""
    if not OPENROUTER_API_KEY:
        raise ValueError("Нет OPENROUTER_API_KEY")
    m = "meta-llama/llama-3.3-70b-instruct:free"
    result = await _call_openai_style(
        OPENROUTER_URL, OPENROUTER_API_KEY, m,
        prompt, system, temperature, "OpenRouter",
        extra_headers={
            "HTTP-Referer": "https://dialectic-edge.bot",
            "X-Title": "Dialectic Edge"
        },
        agent_key=agent_key
    )
    if agent_key:
        _track_model(agent_key, "OpenRouter", m)
    return result


async def _call_openrouter_gemma(prompt: str, system: str, temperature: float, agent_key: str = None) -> str:
    """OpenRouter / Gemma 3 27B — запасной бесплатный fallback.
    Меньше параметров чем Llama, но лучше следует инструкциям."""
    if not OPENROUTER_API_KEY:
        raise ValueError("Нет OPENROUTER_API_KEY")
    m = "google/gemma-3-27b-it:free"
    result = await _call_openai_style(
        OPENROUTER_URL, OPENROUTER_API_KEY, m,
        prompt, system, temperature, "OpenRouter",
        extra_headers={
            "HTTP-Referer": "https://dialectic-edge.bot",
            "X-Title": "Dialectic Edge"
        },
        agent_key=agent_key
    )
    if agent_key:
        _track_model(agent_key, "OpenRouter", m)
    return result


# ── Throttle для Mistral ──────────────────────────────────────────────────────
_LAST_MISTRAL_CALL = 0.0
_mistral_lock: asyncio.Lock | None = None


def _get_mistral_lock() -> asyncio.Lock:
    global _mistral_lock
    if _mistral_lock is None:
        _mistral_lock = asyncio.Lock()
    return _mistral_lock


async def _call_mistral_throttled(prompt: str, system: str, temperature: float, model: str = None, agent_key: str = None) -> str:
    """Throttled Mistral — не более 1 запроса каждые 3 секунды."""
    global _LAST_MISTRAL_CALL
    lock = _get_mistral_lock()
    async with lock:
        now = time.time()
        wait = 3.0 - (now - _LAST_MISTRAL_CALL)
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_MISTRAL_CALL = time.time()
    return await _call_mistral(prompt, system, temperature, model, agent_key=agent_key)


# ── Роутер по агенту ──────────────────────────────────────────────────────────

async def _call_for_agent(agent_key: str, prompt: str, system: str, temperature: float) -> str:
    config = AGENT_MODELS.get(agent_key)

    if config:
        provider = config["provider"]
        model    = config["model"]

        # Основной провайдер
        try:
            if provider == "mistral":
                result = await _call_mistral_throttled(prompt, system, temperature, model, agent_key=agent_key)
            elif provider == "groq":
                result = await _call_groq(prompt, system, temperature, model, agent_key=agent_key)
            else:
                raise ValueError(f"Неизвестный провайдер: {provider}")
            logger.info(f"[{agent_key}] → {provider}/{model} ✅")
            return result
        except Exception as e:
            logger.warning(f"[{agent_key}] → {provider}/{model} ❌ {e}")

        # Synth: Mistral Large → Mistral Small
        if agent_key == "synth" and "large" in model:
            try:
                result = await _call_mistral_throttled(
                    prompt, system, temperature,
                    "mistral-small-latest", agent_key=agent_key
                )
                logger.info(f"[{agent_key}] fallback → mistral/mistral-small-latest ✅")
                return result
            except Exception as e2:
                logger.warning(f"[{agent_key}] synth mistral-small ❌ {e2}")

    # Общий fallback
    return await _call_best_available(prompt, system, temperature, agent_key)


async def _call_best_available(prompt: str, system: str, temperature: float, agent_name: str = "general") -> str:
    """
    Fallback цепочка:
    1. Groq / Llama 3.3 70B     (если лимит не исчерпан)
    2. Mistral Small             (throttled)
    3. OpenRouter / Llama 3.3   (бесплатный, умный)
    4. OpenRouter / Gemma 3 27B (бесплатный, меньше галлюцинаций)
    """
    providers = []

    if GROQ_API_KEY:
        providers.append(("Groq/Llama",
            lambda p, s, t: _call_groq(p, s, t, agent_key=agent_name)))

    if MISTRAL_API_KEY:
        providers.append(("Mistral Small",
            lambda p, s, t: _call_mistral_throttled(p, s, t, agent_key=agent_name)))

    if OPENROUTER_API_KEY:
        providers.append(("OpenRouter/Llama",
            lambda p, s, t: _call_openrouter_llama(p, s, t, agent_key=agent_name)))
        providers.append(("OpenRouter/Gemma",
            lambda p, s, t: _call_openrouter_gemma(p, s, t, agent_key=agent_name)))

    if not providers:
        raise ValueError("Нет API ключей! Добавь GROQ_API_KEY и MISTRAL_API_KEY в Railway")

    last_error = None
    for name, caller in providers:
        try:
            result = await caller(prompt, system, temperature)
            logger.info(f"[{agent_name}] fallback → {name} ✅")
            return result
        except Exception as e:
            logger.warning(f"[{agent_name}] fallback → {name} ❌ {e}")
            last_error = e

    raise RuntimeError(f"Все провайдеры недоступны. Последняя ошибка: {last_error}")


# ── Публичный класс ───────────────────────────────────────────────────────────

class AgentProvider:

    async def bull(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = temperature or AGENT_TEMPERATURE
        return await _call_for_agent("bull", prompt, system, t)

    async def bear(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.4
        return await _call_for_agent("bear", prompt, system, t)

    async def verifier(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = 0.1
        return await _call_for_agent("verifier", prompt, system, t)

    async def synth(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.6
        return await _call_for_agent("synth", prompt, system, t)

    async def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = temperature or AGENT_TEMPERATURE
        return await _call_best_available(prompt, system, t, "general")


ai = AgentProvider()

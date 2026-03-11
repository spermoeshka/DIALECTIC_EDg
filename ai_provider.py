"""
ai_provider.py — Мультипровайдер с роутингом по агентам.

Каждый агент теперь использует СВОЮ модель:
  🐂 Bull      → Groq / Llama 3.3 70B       (быстрый, уверенный)
  🐻 Bear      → Mistral Small               (европейский скептик)
  🔍 Verifier  → OpenRouter / Gemma 3 27B    (нейтральный фактчекер)
  ⚖️ Synth     → OpenRouter / DeepSeek R1    (глубокий синтез с CoT)
  🤖 General   → fallback цепочка как раньше

Бесплатные провайдеры:
  Groq         — console.groq.com        (1000 req/day Llama 70B)
  Mistral      — console.mistral.ai      (500k токенов/мин)
  OpenRouter   — openrouter.ai           (50 req/day, много моделей)
  Together     — together.ai             ($1 при регистрации)
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

TOGETHER_API_KEY   = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_MODEL     = os.getenv("TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free")
TOGETHER_URL       = "https://api.together.xyz/v1/chat/completions"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")
GROQ_URL           = "https://api.groq.com/openai/v1/chat/completions"

# ── Модели по агентам ──────────────────────────────────────────────────────────
# Меняй здесь — всё остальное подстроится автоматически
AGENT_MODELS = {
    "bull":     {"provider": "groq",       "model": "llama-3.3-70b-versatile"},
    "bear":     {"provider": "mistral",    "model": "mistral-small-latest"},
    "verifier": {"provider": "openrouter", "model": "google/gemma-3-27b-it:free"},
    "synth":    {"provider": "openrouter", "model": "deepseek/deepseek-r1-0528:free"},
}


# ── Базовый вызов (OpenAI-совместимый формат) ──────────────────────────────────

async def _call_openai_style(
    url: str,
    api_key: str,
    model: str,
    prompt: str,
    system: str,
    temperature: float,
    name: str,
    extra_headers: dict = None
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

    payload = {
        "model": model,
        "messages": messages,
        "temperature": min(temperature, 1.0),
        "max_tokens": MAX_TOKENS_PER_AGENT,
    }

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, headers=headers, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise RuntimeError(f"{name} HTTP {resp.status}: {err[:300]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()


# ── Конкретные провайдеры ──────────────────────────────────────────────────────

async def _call_groq(prompt: str, system: str, temperature: float, model: str = None) -> str:
    if not GROQ_API_KEY:
        raise ValueError("Нет GROQ_API_KEY")
    return await _call_openai_style(
        GROQ_URL, GROQ_API_KEY,
        model or "llama-3.3-70b-versatile",
        prompt, system, temperature, "Groq"
    )

async def _call_mistral(prompt: str, system: str, temperature: float, model: str = None) -> str:
    if not MISTRAL_API_KEY:
        raise ValueError("Нет MISTRAL_API_KEY")
    return await _call_openai_style(
        MISTRAL_URL, MISTRAL_API_KEY,
        model or MISTRAL_MODEL,
        prompt, system, temperature, "Mistral"
    )

async def _call_openrouter(prompt: str, system: str, temperature: float, model: str = None) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("Нет OPENROUTER_API_KEY")
    return await _call_openai_style(
        OPENROUTER_URL, OPENROUTER_API_KEY,
        model or "google/gemma-3-27b-it:free",
        prompt, system, temperature, "OpenRouter",
        extra_headers={
            "HTTP-Referer": "https://dialectic-edge.bot",
            "X-Title": "Dialectic Edge"
        }
    )

async def _call_together(prompt: str, system: str, temperature: float, model: str = None) -> str:
    if not TOGETHER_API_KEY:
        raise ValueError("Нет TOGETHER_API_KEY")
    return await _call_openai_style(
        TOGETHER_URL, TOGETHER_API_KEY,
        model or TOGETHER_MODEL,
        prompt, system, temperature, "Together"
    )


# ── Роутер по агенту ──────────────────────────────────────────────────────────

PROVIDER_CALLERS = {
    "groq":       _call_groq,
    "mistral":    _call_mistral,
    "openrouter": _call_openrouter,
    "together":   _call_together,
}

async def _call_for_agent(
    agent_key: str,
    prompt: str,
    system: str,
    temperature: float
) -> str:
    """
    Вызывает нужную модель для агента.
    Если основной провайдер недоступен — падает в общий fallback.
    """
    config = AGENT_MODELS.get(agent_key)

    if config:
        provider = config["provider"]
        model = config["model"]
        caller = PROVIDER_CALLERS.get(provider)

        if caller:
            try:
                result = await caller(prompt, system, temperature, model)
                logger.info(f"[{agent_key}] → {provider}/{model} ✅")
                return result
            except Exception as e:
                logger.warning(f"[{agent_key}] → {provider} ❌ {e} — пробую fallback")

    # Fallback: пробуем все доступные провайдеры по порядку
    return await _call_best_available(prompt, system, temperature, agent_key)


async def _call_best_available(
    prompt: str,
    system: str,
    temperature: float,
    agent_name: str = "general"
) -> str:
    """Fallback — пробует провайдеров по порядку."""
    providers = []
    if GROQ_API_KEY:
        providers.append(("Groq",       lambda p, s, t: _call_groq(p, s, t)))
    if MISTRAL_API_KEY:
        providers.append(("Mistral",    lambda p, s, t: _call_mistral_throttled(p, s, t)))
    if OPENROUTER_API_KEY:
        providers.append(("OpenRouter", lambda p, s, t: _call_openrouter(p, s, t)))
    if TOGETHER_API_KEY:
        providers.append(("Together",   lambda p, s, t: _call_together(p, s, t)))

    if not providers:
        raise ValueError(
            "Нет API ключей! Добавь в Railway Variables:\n"
            "GROQ_API_KEY       — console.groq.com (бесплатно)\n"
            "MISTRAL_API_KEY    — console.mistral.ai (бесплатно)\n"
            "OPENROUTER_API_KEY — openrouter.ai (бесплатно)\n"
            "TOGETHER_API_KEY   — together.ai (бесплатно)"
        )

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


# ── Throttle для Mistral (2 sec между запросами) ─────────────────────────────

_LAST_MISTRAL_CALL = 0.0

async def _call_mistral_throttled(prompt: str, system: str, temperature: float, model: str = None) -> str:
    global _LAST_MISTRAL_CALL
    now = time.time()
    wait = 2.0 - (now - _LAST_MISTRAL_CALL)
    if wait > 0:
        await asyncio.sleep(wait)
    _LAST_MISTRAL_CALL = time.time()
    return await _call_mistral(prompt, system, temperature, model)


# ── Публичный класс AgentProvider ────────────────────────────────────────────

class AgentProvider:
    """
    Каждый метод вызывает СВОЮ модель для агента.
    Температуры сохранены как в оригинале.
    """

    async def bull(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = temperature or AGENT_TEMPERATURE          # 0.7 — уверенный оптимист
        return await _call_for_agent("bull", prompt, system, t)

    async def bear(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.4  # 0.28 — холодный скептик
        return await _call_for_agent("bear", prompt, system, t)

    async def verifier(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = 0.1                                        # минимум — только факты
        return await _call_for_agent("verifier", prompt, system, t)

    async def synth(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.6  # 0.42 — взвешенный
        return await _call_for_agent("synth", prompt, system, t)

    async def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        """General вызов — fallback цепочка."""
        t = temperature or AGENT_TEMPERATURE
        return await _call_best_available(prompt, system, t, "general")


ai = AgentProvider()

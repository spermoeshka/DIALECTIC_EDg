"""
ai_provider.py — Мультипровайдер с роутингом по агентам.

Каждый агент теперь использует СВОЮ модель:
  🐂 Bull      → Groq / Llama 3.3 70B       (быстрый, уверенный)
  🐻 Bear      → Mistral Small               (европейский скептик)
  🔍 Verifier  → Groq / Llama 3.3 70B       (нейтральный фактчекер)
  ⚖️ Synth     → Mistral Large               (глубокий синтез)
  🤖 General   → fallback цепочка как раньше
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
AGENT_MODELS = {
    "bull":     {"provider": "groq",    "model": "llama-3.3-70b-versatile"},
    "verifier": {"provider": "groq",    "model": "llama-3.3-70b-versatile"},
    "bear":     {"provider": "mistral", "model": "mistral-small-latest"},
    "synth":    {"provider": "mistral", "model": "mistral-large-latest"},
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

    # Лимиты токенов по агентам
    # synth=5000 — полный синтез с эффектами 2-3 порядка, речами лидеров, сценариями
    _AGENT_MAX_TOKENS = {
        "bull":     1200,
        "bear":     1200,
        "verifier": 800,
        "synth":    5000,   # ← было 3000, обрезало "Простыми словами" и конец плана
    }
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
    return await _call_openai_style(
        GROQ_URL, GROQ_API_KEY,
        model or "llama-3.3-70b-versatile",
        prompt, system, temperature, "Groq",
        agent_key=agent_key
    )

async def _call_mistral(prompt: str, system: str, temperature: float, model: str = None, agent_key: str = None) -> str:
    if not MISTRAL_API_KEY:
        raise ValueError("Нет MISTRAL_API_KEY")
    return await _call_openai_style(
        MISTRAL_URL, MISTRAL_API_KEY,
        model or MISTRAL_MODEL,
        prompt, system, temperature, "Mistral",
        agent_key=agent_key
    )

async def _call_openrouter(prompt: str, system: str, temperature: float, model: str = None, agent_key: str = None) -> str:
    if not OPENROUTER_API_KEY:
        raise ValueError("Нет OPENROUTER_API_KEY")
    return await _call_openai_style(
        OPENROUTER_URL, OPENROUTER_API_KEY,
        model or "google/gemma-3-27b-it:free",
        prompt, system, temperature, "OpenRouter",
        extra_headers={
            "HTTP-Referer": "https://dialectic-edge.bot",
            "X-Title": "Dialectic Edge"
        },
        agent_key=agent_key
    )

async def _call_together(prompt: str, system: str, temperature: float, model: str = None, agent_key: str = None) -> str:
    if not TOGETHER_API_KEY:
        raise ValueError("Нет TOGETHER_API_KEY")
    return await _call_openai_style(
        TOGETHER_URL, TOGETHER_API_KEY,
        model or TOGETHER_MODEL,
        prompt, system, temperature, "Together",
        agent_key=agent_key
    )


# ── Роутер по агенту ──────────────────────────────────────────────────────────

PROVIDER_CALLERS = {
    "groq":       _call_groq,
    "mistral":    _call_mistral,
    "openrouter": _call_openrouter,
    "together":   _call_together,
}

async def _call_for_agent(agent_key: str, prompt: str, system: str, temperature: float) -> str:
    config = AGENT_MODELS.get(agent_key)

    if config:
        provider = config["provider"]
        model = config["model"]
        caller = PROVIDER_CALLERS.get(provider)

        if caller:
            try:
                result = await caller(prompt, system, temperature, model, agent_key=agent_key)
                logger.info(f"[{agent_key}] → {provider}/{model} ✅")
                return result
            except Exception as e:
                logger.warning(f"[{agent_key}] → {provider}/{model} ❌ {e}")
                if agent_key == "synth" and "large" in model:
                    try:
                        fallback_model = "mistral-small-latest"
                        result = await caller(prompt, system, temperature, fallback_model, agent_key=agent_key)
                        logger.info(f"[{agent_key}] fallback → {provider}/{fallback_model} ✅")
                        return result
                    except Exception as e2:
                        logger.warning(f"[{agent_key}] synth fallback ❌ {e2}")
                logger.warning(f"[{agent_key}] → пробую общий fallback")

    return await _call_best_available(prompt, system, temperature, agent_key)


async def _call_best_available(prompt: str, system: str, temperature: float, agent_name: str = "general") -> str:
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


# ── Throttle для Mistral ──────────────────────────────────────────────────────

_LAST_MISTRAL_CALL = 0.0

async def _call_mistral_throttled(prompt: str, system: str, temperature: float, model: str = None) -> str:
    global _LAST_MISTRAL_CALL
    now = time.time()
    wait = 2.0 - (now - _LAST_MISTRAL_CALL)
    if wait > 0:
        await asyncio.sleep(wait)
    _LAST_MISTRAL_CALL = time.time()
    return await _call_mistral(prompt, system, temperature, model)


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

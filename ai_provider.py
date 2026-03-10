"""
ai_provider.py — Только Groq. Бесплатно. Работает из России.

Groq даёт 14400 запросов/день бесплатно.
Модель llama-3.3-70b — уровень GPT-4, без галлюцинаций как у мелких моделей.

Агенты разделены по температуре:
  🐂 Bull     — высокая температура (творческий, ищет возможности)
  🐻 Bear     — низкая температура (холодный, ищет риски)
  🔍 Verifier — минимальная температура (точный, меньше галлюцинаций)
  ⚖️ Synth    — средняя температура (взвешенный)
"""

import logging
import os
import aiohttp

from config import MAX_TOKENS_PER_AGENT, AGENT_TEMPERATURE

logger = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=90)

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"


async def _call_groq(prompt: str, system: str, temperature: float) -> str:
    if not GROQ_API_KEY:
        raise ValueError("GROQ_API_KEY не задан в .env")

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": messages,
        "temperature": min(temperature, 1.0),
        "max_tokens": MAX_TOKENS_PER_AGENT,
    }
    async with aiohttp.ClientSession() as s:
        async with s.post(GROQ_URL, json=payload,
                          headers=headers, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise RuntimeError(f"Groq {resp.status}: {err[:200]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()


class AgentProvider:
    """
    Все агенты на Groq — бесплатно 14400 запросов/день.
    Разные роли достигаются через разные промпты и температуры.
    """

    async def bull(self, prompt: str, system: str = "",
                   temperature: float = None) -> str:
        t = temperature or AGENT_TEMPERATURE  # 0.7 — творческий
        logger.info("🐂 Bull → Groq Llama (temp=0.7)")
        return await _call_groq(prompt, system, t)

    async def bear(self, prompt: str, system: str = "",
                   temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.5  # 0.35 — холодный скептик
        logger.info("🐻 Bear → Groq Llama (temp=0.35)")
        return await _call_groq(prompt, system, t)

    async def verifier(self, prompt: str, system: str = "",
                       temperature: float = None) -> str:
        t = 0.1  # минимум — факты должны быть точными
        logger.info("🔍 Verifier → Groq Llama (temp=0.1)")
        return await _call_groq(prompt, system, t)

    async def synth(self, prompt: str, system: str = "",
                    temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.6  # 0.42 — взвешенный
        logger.info("⚖️ Synth → Groq Llama (temp=0.42)")
        return await _call_groq(prompt, system, t)

    async def complete(self, prompt: str, system: str = "",
                       temperature: float = None) -> str:
        t = temperature or AGENT_TEMPERATURE
        return await _call_groq(prompt, system, t)


ai = AgentProvider()
"""
ai_provider.py — Gemini на Railway (сервер в Европе).

Все агенты на Gemini — работает из России через Railway.
Разные роли через разные температуры и промпты.

🐂 Bull     — gemini-2.0-flash, temp=0.9 (творческий)
🐻 Bear     — gemini-2.0-flash, temp=0.3 (холодный скептик)
🔍 Verifier — gemini-2.0-flash, temp=0.1 (точный, меньше галлюцинаций)
⚖️ Synth    — gemini-2.0-flash, temp=0.5 (взвешенный)
"""

import logging
import os
import aiohttp

from config import MAX_TOKENS_PER_AGENT, AGENT_TEMPERATURE

logger = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=90)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_URL     = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


async def _call_gemini(prompt: str, system: str, temperature: float) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY не задан в переменных Railway")

    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"

    payload = {
        "contents": [{"parts": [{"text": full_prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": MAX_TOKENS_PER_AGENT,
        }
    }

    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=payload, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                err = await resp.text()
                raise RuntimeError(f"Gemini {resp.status}: {err[:200]}")
            data = await resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()


class AgentProvider:
    async def bull(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = temperature or AGENT_TEMPERATURE  # 0.7
        logger.info("🐂 Bull → Gemini Flash (temp=0.7)")
        return await _call_gemini(prompt, system, t)

    async def bear(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.4  # 0.28 — холодный
        logger.info("🐻 Bear → Gemini Flash (temp=0.28)")
        return await _call_gemini(prompt, system, t)

    async def verifier(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = 0.1  # минимум — факты
        logger.info("🔍 Verifier → Gemini Flash (temp=0.1)")
        return await _call_gemini(prompt, system, t)

    async def synth(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = (temperature or AGENT_TEMPERATURE) * 0.6  # 0.42
        logger.info("⚖️ Synth → Gemini Flash (temp=0.42)")
        return await _call_gemini(prompt, system, t)

    async def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        t = temperature or AGENT_TEMPERATURE
        return await _call_gemini(prompt, system, t)


ai = AgentProvider()

"""
ai_provider.py — Мультипровайдер с роутингом по агентам.

Переменные окружения (Railway / .env):
  AI_DEBATE_PRIMARY — кто первым отвечает в дебатах:
      openrouter | together | groq | mistral | gemini | cerebras | mixed
  Все свободные модели! OPENROUTER_API_KEY и TOGETHER_API_KEY обязательны.

Fallback цепь: Cerebras → OpenRouter → Together → Groq → Mistral → Gemini
"""

import logging
import os
import re
import time
import asyncio
import aiohttp

from config import MAX_TOKENS_PER_AGENT, AGENT_TEMPERATURE

logger = logging.getLogger(__name__)
TIMEOUT = aiohttp.ClientTimeout(total=180)

# ── Ключи ──────────────────────────────────────────────────────────────────────
MISTRAL_API_KEY    = os.getenv("MISTRAL_API_KEY", "")
MISTRAL_API_KEY_2  = os.getenv("MISTRAL_API_KEY_2", "")
MISTRAL_MODEL      = os.getenv("MISTRAL_MODEL", "mistral-small-latest")
MISTRAL_URL        = "https://api.mistral.ai/v1/chat/completions"

OPENROUTER_API_KEY   = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_KEY_2 = os.getenv("OPENROUTER_API_KEY_2", "")
OPENROUTER_URL       = "https://openrouter.ai/api/v1/chat/completions"

TOGETHER_API_KEY   = os.getenv("TOGETHER_API_KEY", "")
TOGETHER_API_KEY_2 = os.getenv("TOGETHER_API_KEY_2", "")
TOGETHER_URL       = "https://api.together.xyz/v1/chat/completions"

GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
GROQ_API_KEY_2 = os.getenv("GROQ_API_KEY_2", "")
GROQ_API_KEY_3 = os.getenv("GROQ_API_KEY_3", "")
GROQ_URL       = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL     = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip() or "llama-3.3-70b-versatile"

OPENROUTER_MODEL = os.getenv(
    "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
).strip() or "meta-llama/llama-3.3-70b-instruct:free"
OPENROUTER_SYNTH_MODEL = os.getenv("OPENROUTER_SYNTH_MODEL", "").strip() or OPENROUTER_MODEL

TOGETHER_MODEL = os.getenv(
    "TOGETHER_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"
).strip() or "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL   = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip() or "gemini-1.5-flash"

# ── Cerebras (бесплатный, ~1000 токен/сек!) ───────────────────────────────────
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
# ФИX 1: правильная модель (llama-3.1-70b-instruct не существует → 404)
CEREBRAS_MODEL   = os.getenv("CEREBRAS_MODEL", "llama-3.3-70b").strip() or "llama-3.3-70b"
CEREBRAS_URL     = "https://api.cerebras.ai/v1/chat/completions"

# ── Трекинг моделей ───────────────────────────────────────────────────────────
MODELS_USED: dict = {}

def _track_model(agent_key: str, provider: str, model: str):
    labels = {
        # ФИX 2: добавлена правильная метка для Cerebras
        "llama-3.3-70b":                           "Cerebras/Llama 3.3 70B 🚀",
        "llama-3.1-70b-instruct":                  "Cerebras/Llama 3.1 70B",
        "mistral-small-latest":                    "Mistral Small",
        "mistral-large-latest":                    "Mistral Large",
        "llama-3.3-70b-versatile":                 "Groq/Llama 3.3 70B",
        "meta-llama/llama-3.3-70b-instruct:free":  "OpenRouter/Llama 3.3 70B",
        "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free": "Together/Llama 3.3 70B",
        "google/gemma-3-27b-it:free":              "OpenRouter/Gemma 3 27B",
    }
    label = labels.get(model, f"{provider}/{model}")
    MODELS_USED[agent_key] = label
    logger.info(f"[{agent_key}] использует: {label}")


def _debate_primary_env() -> str:
    return os.getenv("AI_DEBATE_PRIMARY", "mixed").strip().lower() or "mixed"


def _can_use_primary(name: str) -> bool:
    if name == "cerebras":  return bool(CEREBRAS_API_KEY)
    if name == "mistral":   return bool(MISTRAL_API_KEY or MISTRAL_API_KEY_2)
    if name == "groq":      return bool(GROQ_API_KEY or GROQ_API_KEY_2 or GROQ_API_KEY_3)
    if name == "openrouter":return bool(OPENROUTER_API_KEY or OPENROUTER_API_KEY_2)
    if name == "together":  return bool(TOGETHER_API_KEY or TOGETHER_API_KEY_2)
    if name == "gemini":    return bool(GEMINI_API_KEY)
    return False


def _resolve_agent_models() -> dict:
    want = _debate_primary_env()
    valid = ("openrouter", "together", "mistral", "groq", "gemini", "cerebras", "mixed")
    if want not in valid:
        logger.warning("AI_DEBATE_PRIMARY=%s неизвестен — использую mixed", want)
        want = "mixed"
    if want != "mixed" and not _can_use_primary(want):
        logger.warning("AI_DEBATE_PRIMARY=%s недоступен — откат на следующий", want)
        for p in ("cerebras", "openrouter", "together", "groq", "mistral", "gemini"):
            if _can_use_primary(p):
                want = p
                break

    mm    = os.getenv("MISTRAL_MODEL", MISTRAL_MODEL).strip() or MISTRAL_MODEL
    syn_m = os.getenv("MISTRAL_SYNTH_MODEL", "mistral-large-latest").strip() or "mistral-large-latest"

    def _model_for(p):
        return {
            "cerebras":   CEREBRAS_MODEL,
            "groq":       GROQ_MODEL,
            "together":   TOGETHER_MODEL,
            "openrouter": OPENROUTER_MODEL,
            "mistral":    mm,
            "gemini":     GEMINI_MODEL,
        }.get(p, mm)

    if want == "cerebras":
        m = {a: {"provider": "cerebras", "model": CEREBRAS_MODEL}
             for a in ("bull", "bear", "verifier", "synth")}
    elif want == "openrouter":
        m = {a: {"provider": "openrouter", "model": OPENROUTER_MODEL}
             for a in ("bull", "bear", "verifier")}
        m["synth"] = {"provider": "openrouter", "model": OPENROUTER_SYNTH_MODEL}
    elif want == "together":
        m = {a: {"provider": "together", "model": TOGETHER_MODEL}
             for a in ("bull", "bear", "verifier", "synth")}
    elif want == "groq":
        m = {a: {"provider": "groq", "model": GROQ_MODEL}
             for a in ("bull", "bear", "verifier", "synth")}
    elif want == "gemini":
        m = {a: {"provider": "gemini", "model": GEMINI_MODEL}
             for a in ("bull", "bear", "verifier", "synth")}
    elif want == "mistral":
        m = {a: {"provider": "mistral", "model": mm}
             for a in ("bull", "bear", "verifier")}
        m["synth"] = {"provider": "mistral", "model": syn_m}
    else:  # mixed — настоящая диалектика!
        # ФИX 3: Cerebras первым для Bull (быстрый и бесплатный)
        def pick(*prefs):
            for p in prefs:
                if _can_use_primary(p):
                    return p
            return "mistral"

        bull_p = pick("cerebras", "openrouter", "together", "groq")
        bear_p = pick("groq", "cerebras", "together", "openrouter")
        ver_p  = pick("together", "openrouter", "cerebras", "groq")
        syn_p  = pick("mistral", "groq", "cerebras")

        m = {
            "bull":     {"provider": bull_p, "model": _model_for(bull_p)},
            "bear":     {"provider": bear_p, "model": _model_for(bear_p)},
            "verifier": {"provider": ver_p,  "model": _model_for(ver_p)},
            "synth":    {"provider": syn_p,
                         "model": syn_m if syn_p == "mistral" else _model_for(syn_p)},
        }
        logger.info(
            "Mixed: Bull=%s Bear=%s Verifier=%s Synth=%s",
            bull_p, bear_p, ver_p, syn_p
        )

    logger.info("Дебаты: первичный провайдер = %s", want)
    return m


def get_models_summary() -> str:
    if not MODELS_USED:
        return "🐂 Bull | 🐻 Bear | 🔍 Verifier | ⚖️ Synth"
    return (
        f"🐂 Bull = {MODELS_USED.get('bull','?')} | "
        f"🐻 Bear = {MODELS_USED.get('bear','?')} | "
        f"🔍 Verifier = {MODELS_USED.get('verifier','?')} | "
        f"⚖️ Synth = {MODELS_USED.get('synth','?')}"
    )


AGENT_MODELS = _resolve_agent_models()

_AGENT_MAX_TOKENS = {
    "bull":     2500,
    "bear":     2500,
    "verifier": 1000,
    "synth":    6000,
}


# ── Базовый вызов ──────────────────────────────────────────────────────────────

async def _call_openai_style(
    url: str, api_key: str, model: str,
    prompt: str, system: str, temperature: float, name: str,
    extra_headers: dict = None, agent_key: str = None
) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
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


# ── Провайдеры ────────────────────────────────────────────────────────────────

async def _call_cerebras(prompt: str, system: str, temperature: float,
                         model: str = None, agent_key: str = None) -> str:
    if not CEREBRAS_API_KEY:
        raise ValueError("Нет CEREBRAS_API_KEY")
    m = model or CEREBRAS_MODEL
    result = await _call_openai_style(
        CEREBRAS_URL, CEREBRAS_API_KEY, m,
        prompt, system, temperature, "Cerebras",
        agent_key=agent_key
    )
    if agent_key:
        _track_model(agent_key, "Cerebras", m)
    return result


async def _call_groq(prompt: str, system: str, temperature: float,
                     model: str = None, agent_key: str = None) -> str:
    if not any([GROQ_API_KEY, GROQ_API_KEY_2, GROQ_API_KEY_3]):
        raise ValueError("Нет GROQ_API_KEY")

    m = model or GROQ_MODEL
    keys_to_try = [(n, k) for n, k in [
        ("Groq#1", GROQ_API_KEY),
        ("Groq#2", GROQ_API_KEY_2),
        ("Groq#3", GROQ_API_KEY_3),
    ] if k]

    last_err = None
    for key_name, key in keys_to_try:
        try:
            result = await _call_openai_style(
                GROQ_URL, key, m, prompt, system, temperature, key_name,
                agent_key=agent_key
            )
            if agent_key:
                _track_model(agent_key, key_name, m)
            logger.info(f"Groq {key_name} ✅")
            return result
        except RuntimeError as e:
            err_s = str(e)
            if "429" in err_s:
                idx = keys_to_try.index((key_name, key))
                has_next = idx < len(keys_to_try) - 1
                if has_next:
                    logger.warning(f"{key_name} лимит → сразу пробую следующий ключ...")
                    last_err = e
                    continue
                else:
                    wait_m = re.search(r"try again in ([\d.]+)\s*s", err_s, re.I)
                    if wait_m:
                        sec = min(30.0, float(wait_m.group(1)) + 1.0)
                        logger.warning(f"{key_name} последний ключ — жду {sec:.1f}s...")
                        await asyncio.sleep(sec)
                        try:
                            result = await _call_openai_style(
                                GROQ_URL, key, m, prompt, system,
                                temperature, key_name, agent_key=agent_key
                            )
                            if agent_key:
                                _track_model(agent_key, key_name, m)
                            logger.info(f"Groq {key_name} ✅ (после паузы)")
                            return result
                        except RuntimeError as e2:
                            last_err = e2
                    logger.warning(f"{key_name} лимит исчерпан")
                    last_err = e
                    continue
            raise
    raise RuntimeError(f"Все Groq ключи исчерпаны: {last_err}")


async def _call_mistral(prompt: str, system: str, temperature: float,
                        model: str = None, agent_key: str = None) -> str:
    m = model or MISTRAL_MODEL
    keys_to_try = [(n, k) for n, k in [
        ("Mistral#1", MISTRAL_API_KEY),
        ("Mistral#2", MISTRAL_API_KEY_2),
    ] if k]
    if not keys_to_try:
        raise ValueError("Нет MISTRAL_API_KEY")
    last_err = None
    for key_name, key in keys_to_try:
        try:
            result = await _call_openai_style(
                MISTRAL_URL, key, m, prompt, system, temperature, key_name,
                agent_key=agent_key
            )
            if agent_key:
                _track_model(agent_key, key_name, m)
            logger.info(f"{key_name} ✅")
            return result
        except RuntimeError as e:
            if "429" in str(e):
                logger.warning(f"{key_name} лимит — пробую Mistral#2...")
                last_err = e
                await asyncio.sleep(2.5)
                continue
            raise
    raise RuntimeError(f"Все Mistral ключи исчерпаны: {last_err}")


_OR_HEADERS = {
    "HTTP-Referer": "https://dialectic-edge.bot",
    "X-Title": "Dialectic Edge",
}


async def _call_openrouter_model(prompt: str, system: str, temperature: float,
                                  model: str, agent_key: str = None) -> str:
    keys_try = [(n, k) for n, k in [
        ("OpenRouter", OPENROUTER_API_KEY),
        ("OpenRouter#2", OPENROUTER_API_KEY_2),
    ] if k]
    if not keys_try:
        raise ValueError("Нет OPENROUTER_API_KEY")
    last_err = None
    for key_name, key in keys_try:
        try:
            result = await _call_openai_style(
                OPENROUTER_URL, key, model, prompt, system, temperature,
                key_name, extra_headers=_OR_HEADERS, agent_key=agent_key
            )
            if agent_key:
                _track_model(agent_key, key_name, model)
            return result
        except RuntimeError as e:
            if "429" in str(e) or "402" in str(e):
                logger.warning(f"{key_name} лимит OpenRouter — следующий ключ...")
                last_err = e
                continue
            raise
    raise RuntimeError(f"Все OpenRouter ключи исчерпаны: {last_err}")


async def _call_openrouter_llama(prompt: str, system: str, temperature: float,
                                  agent_key: str = None) -> str:
    return await _call_openrouter_model(
        prompt, system, temperature,
        "meta-llama/llama-3.3-70b-instruct:free", agent_key
    )


async def _call_openrouter_gemma(prompt: str, system: str, temperature: float,
                                  agent_key: str = None) -> str:
    return await _call_openrouter_model(
        prompt, system, temperature,
        "google/gemma-3-27b-it:free", agent_key
    )


async def _call_gemini(prompt: str, system: str, temperature: float,
                       agent_key: str = None) -> str:
    if not GEMINI_API_KEY:
        raise ValueError("Нет GEMINI_API_KEY")
    m = GEMINI_MODEL
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
    max_tok = _AGENT_MAX_TOKENS.get(agent_key, MAX_TOKENS_PER_AGENT)
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": min(temperature, 1.0), "maxOutputTokens": max_tok},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, params={"key": GEMINI_API_KEY},
                          json=body, timeout=TIMEOUT) as resp:
            raw = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Gemini HTTP {resp.status}: {raw[:400]}")
            data = await resp.json()
            cand = data.get("candidates") or []
            if not cand:
                raise RuntimeError(f"Gemini: нет candidates")
            parts_g = cand[0].get("content", {}).get("parts") or []
            if not parts_g or not parts_g[0].get("text"):
                raise RuntimeError("Gemini: пустой текст")
            out = parts_g[0]["text"].strip()
            if agent_key:
                _track_model(agent_key, "Gemini", m)
            return out


async def _call_together(prompt: str, system: str, temperature: float,
                         model: str = None, agent_key: str = None) -> str:
    m = model or TOGETHER_MODEL
    keys_to_try = [(n, k) for n, k in [
        ("Together#1", TOGETHER_API_KEY),
        ("Together#2", TOGETHER_API_KEY_2),
    ] if k]
    if not keys_to_try:
        raise ValueError("Нет TOGETHER_API_KEY")
    last_err = None
    for key_name, key in keys_to_try:
        try:
            result = await _call_openai_style(
                TOGETHER_URL, key, m, prompt, system, temperature, key_name,
                agent_key=agent_key
            )
            if agent_key:
                _track_model(agent_key, key_name, m)
            logger.info(f"{key_name} ✅")
            return result
        except RuntimeError as e:
            if "429" in str(e):
                logger.warning(f"{key_name} лимит — пробую Together#2...")
                last_err = e
                continue
            raise
    raise RuntimeError(f"Все Together ключи исчерпаны: {last_err}")


# ── Throttle для Mistral ──────────────────────────────────────────────────────
_LAST_MISTRAL_CALL = 0.0
_mistral_lock = None

def _get_mistral_lock() -> asyncio.Lock:
    global _mistral_lock
    if _mistral_lock is None:
        _mistral_lock = asyncio.Lock()
    return _mistral_lock

async def _call_mistral_throttled(prompt: str, system: str, temperature: float,
                                   model: str = None, agent_key: str = None) -> str:
    global _LAST_MISTRAL_CALL
    lock = _get_mistral_lock()
    async with lock:
        now = time.time()
        wait = 3.0 - (now - _LAST_MISTRAL_CALL)
        if wait > 0:
            await asyncio.sleep(wait)
        _LAST_MISTRAL_CALL = time.time()
    return await _call_mistral(prompt, system, temperature, model, agent_key=agent_key)


# ── Роутер ────────────────────────────────────────────────────────────────────

async def _call_for_agent(agent_key: str, prompt: str, system: str, temperature: float) -> str:
    config = AGENT_MODELS.get(agent_key)

    if config:
        provider = config["provider"]
        model    = config["model"]
        try:
            if provider == "cerebras":
                result = await _call_cerebras(prompt, system, temperature, model, agent_key=agent_key)
            elif provider == "mistral":
                result = await _call_mistral_throttled(prompt, system, temperature, model, agent_key=agent_key)
            elif provider == "groq":
                result = await _call_groq(prompt, system, temperature, model, agent_key=agent_key)
            elif provider == "openrouter":
                result = await _call_openrouter_model(prompt, system, temperature, model, agent_key=agent_key)
            elif provider == "together":
                result = await _call_together(prompt, system, temperature, model, agent_key=agent_key)
            elif provider == "gemini":
                result = await _call_gemini(prompt, system, temperature, agent_key=agent_key)
            else:
                raise ValueError(f"Неизвестный провайдер: {provider}")
            logger.info(f"[{agent_key}] → {provider}/{model} ✅")
            return result
        except Exception as e:
            logger.warning(f"[{agent_key}] → {provider}/{model} ❌ {e}")

        if (agent_key == "synth" and provider == "mistral"
                and model and "large" in model.lower()):
            try:
                result = await _call_mistral_throttled(
                    prompt, system, temperature, "mistral-small-latest", agent_key=agent_key
                )
                logger.info(f"[{agent_key}] fallback → mistral-small ✅")
                return result
            except Exception as e2:
                logger.warning(f"[{agent_key}] synth mistral-small ❌ {e2}")

    skip_p = frozenset({config["provider"]} if config else [])
    return await _call_best_available(prompt, system, temperature, agent_key, skip_providers=skip_p)


async def _call_best_available(
    prompt: str, system: str, temperature: float,
    agent_name: str = "general", *, skip_providers: frozenset | None = None,
) -> str:
    """
    Fallback цепочка: Cerebras → OpenRouter → Together → Groq → Mistral → Gemini
    """
    skip = set(skip_providers or [])
    providers = []

    # ФИX 4: Cerebras добавлен в fallback цепочку (первым — самый быстрый)
    if "cerebras" not in skip and CEREBRAS_API_KEY:
        providers.append(("Cerebras/Llama",
            lambda p, s, t: _call_cerebras(p, s, t, agent_key=agent_name)))

    if "openrouter" not in skip and (OPENROUTER_API_KEY or OPENROUTER_API_KEY_2):
        providers.append(("OpenRouter/Llama",
            lambda p, s, t: _call_openrouter_llama(p, s, t, agent_key=agent_name)))
        providers.append(("OpenRouter/Gemma",
            lambda p, s, t: _call_openrouter_gemma(p, s, t, agent_key=agent_name)))

    if "together" not in skip and (TOGETHER_API_KEY or TOGETHER_API_KEY_2):
        providers.append(("Together/Llama",
            lambda p, s, t: _call_together(p, s, t, agent_key=agent_name)))

    if "groq" not in skip and (GROQ_API_KEY or GROQ_API_KEY_2 or GROQ_API_KEY_3):
        providers.append(("Groq/Llama",
            lambda p, s, t: _call_groq(p, s, t, agent_key=agent_name)))

    if "mistral" not in skip and (MISTRAL_API_KEY or MISTRAL_API_KEY_2):
        providers.append(("Mistral Small",
            lambda p, s, t: _call_mistral_throttled(p, s, t, agent_key=agent_name)))

    if "gemini" not in skip and GEMINI_API_KEY:
        providers.append(("Gemini",
            lambda p, s, t: _call_gemini(p, s, t, agent_key=agent_name)))

    if not providers:
        raise ValueError("Нет API ключей! Добавь хотя бы один из: CEREBRAS_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, MISTRAL_API_KEY")

    last_error = None
    for name, caller in providers:
        try:
            result = await caller(prompt, system, temperature)
            logger.info(f"[{agent_name}] fallback → {name} ✅")
            return result
        except Exception as e:
            logger.warning(f"[{agent_name}] fallback → {name} ❌ {e}")
            last_error = e

    raise RuntimeError(f"Все провайдеры недоступны: {last_error}")


# ── Публичный класс ───────────────────────────────────────────────────────────

class AgentProvider:

    async def bull(self, prompt: str, system: str = "", temperature: float = None) -> str:
        return await _call_for_agent("bull", prompt, system, temperature or AGENT_TEMPERATURE)

    async def bear(self, prompt: str, system: str = "", temperature: float = None) -> str:
        return await _call_for_agent("bear", prompt, system, (temperature or AGENT_TEMPERATURE) * 0.4)

    async def verifier(self, prompt: str, system: str = "", temperature: float = None) -> str:
        return await _call_for_agent("verifier", prompt, system, 0.1)

    async def synth(self, prompt: str, system: str = "", temperature: float = None) -> str:
        return await _call_for_agent("synth", prompt, system, (temperature or AGENT_TEMPERATURE) * 0.6)

    async def complete(self, prompt: str, system: str = "", temperature: float = None) -> str:
        return await _call_best_available(
            prompt, system, temperature or AGENT_TEMPERATURE,
            "general", skip_providers=frozenset()
        )


ai = AgentProvider()

"""
Утилиты для обработки текста и парсинга отчётов.
"""

import re
from datetime import datetime
from typing import Optional


def clean_markdown(text: str) -> str:
    lines = text.split("\n")
    clean_lines = []
    for line in lines:
        if line.count("*") % 2 != 0:
            line = line.replace("*", "")
        if line.count("_") % 2 != 0:
            line = line.replace("_", "")
        if line.count("`") % 2 != 0:
            line = line.replace("`", "")
        clean_lines.append(line)
    return "\n".join(clean_lines)


def debate_plain_text(text: str) -> str:
    t = clean_markdown(text)
    t = re.sub(r"[*_`#]", "", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def strip_digest_summary_text(text: str) -> str:
    if not text or not text.strip():
        return text
    out: list[str] = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[-_*═─]{3,}\s*$", s):
            continue
        s = re.sub(r"^#{1,6}\s*", "", s)
        s = re.sub(r"\*+", "", s)
        s = re.sub(r"_+", "", s)
        s = re.sub(r"`+", "", s)
        s = s.strip()
        if s:
            out.append(s)
    return "\n".join(out) if out else text.strip()


def split_message(text: str, max_len: int = 3800) -> list[str]:
    text = re.sub(r'[*_`#]', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()

    if len(text) <= max_len:
        return [text]
    
    chunks = []
    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1 or split_at < max_len // 2:
            split_at = text.rfind(" ", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip("\n ")
    if text.strip():
        chunks.append(text.strip())
    return chunks


def signal_to_stars(confidence) -> str:
    mapping = {"HIGH": 0.85, "MEDIUM": 0.55, "LOW": 0.25, "EXTREME": 0.95}
    if isinstance(confidence, str):
        confidence = mapping.get(confidence.upper(), 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    stars = max(1, min(5, round(confidence * 5)))
    return "⭐" * stars + "☆" * (5 - stars)


def extract_signal_pct_and_stars(report: str) -> tuple[int, str]:
    m = re.search(r"Уровень\s+сигнала[^\d(]*\((\d+)%", report, re.IGNORECASE)
    if not m:
        m = re.search(r"📶[^\n]{0,160}\((\d+)%", report)
    pct = int(m.group(1)) if m else 50
    pct = max(0, min(100, pct))
    return pct, signal_to_stars(pct / 100)


SIGNAL_PCT_EXPLAINED = (
    "Число % — уверенность FinBERT в тоне новостей "
    "(EXTREME≈95%, HIGH≈85%, MEDIUM≈55%, LOW≈25%), "
    "не прогноз «рынок пойдёт вверх/вниз». Звёзды — наглядная шкала той же метрики.\n"
    "Если ниже FinBERT = NEUTRAL/MIXED, процент — насколько модель уверена именно в этой метке тона, "
    "а не «сила бычьего/медвежьего тренда»."
)

_SYNTH_START_MARKERS = (
    "⚖️ *ВЕРДИКТ И ТОРГОВЫЙ ПЛАН*",
    "⚖️ ВЕРДИКТ И ТОРГОВЫЙ ПЛАН",
    "⚖️ *ИТОГОВЫЙ СИНТЕЗ И РЕКОМЕНДАЦИИ*",
    "⚖️ ИТОГОВЫЙ СИНТЕЗ И РЕКОМЕНДАЦИИ",
    "ИТОГОВЫЙ СИНТЕЗ",
)
_DEBATE_START_MARKERS = (
    "🗣 *ДЕБАТЫ АГЕНТОВ*",
    "🗣 *ХОД ДЕБАТОВ*",
    "🗣 ХОД ДЕБАТОВ",
    "🗣 ДЕБАТЫ АГЕНТОВ",
)
_ROUND_HEADER_RE = re.compile(r"──\s*Раунд\s+\d+")

_DEBATE_START_RES = (
    re.compile(r"🗣\s*\*?\s*ХОД\s+ДЕБАТОВ", re.IGNORECASE),
    re.compile(r"🗣\s*\*?\s*ДЕБАТЫ\s+АГЕНТОВ", re.IGNORECASE),
    re.compile(r"\*?──\*?\s*Раунд\s+1\b"),
    re.compile(r"──\s*Раунд\s+1\b"),
    re.compile(r"🐂\s*Bull\s+Researcher"),
)


def find_debate_start_index(text: str) -> Optional[int]:
    hit = _find_first_marker(text, _DEBATE_START_MARKERS)
    if hit:
        return hit[0]
    best: Optional[int] = None
    for rx in _DEBATE_START_RES:
        m = rx.search(text)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def _find_first_marker(text: str, markers: tuple[str, ...]) -> Optional[tuple[int, str]]:
    best: Optional[tuple[int, str]] = None
    for m in markers:
        i = text.find(m)
        if i != -1 and (best is None or i < best[0]):
            best = (i, m)
    return best


def parse_report_parts(report: str) -> dict:
    parts = {
        "header": "",
        "rounds": [],
        "synthesis": "",
        "disclaimer": "",
        "full": report
    }

    for disc_marker in [
        "─────────────────────────\n🤝 Честно о боте:",
        "─────────────────────────\n🤝 *Честно о боте:*",
        "🤝 Честно о боте:",
        "🤝 *Честно о боте:*",
    ]:
        if disc_marker in report:
            idx = report.find(disc_marker)
            parts["disclaimer"] = report[idx:]
            report = report[:idx]
            break

    synth_hit = _find_first_marker(report, _SYNTH_START_MARKERS)
    if synth_hit:
        idx, _ = synth_hit
        parts["synthesis"] = report[idx:].strip()
        report = report[:idx]

    round_markers_legacy = ("── Раунд 1:", "── Раунд 2:", "── Раунд 3:")

    debate_idx = find_debate_start_index(report)
    if debate_idx is not None:
        parts["header"] = report[:debate_idx].strip()
        debate_section = report[debate_idx:]

        current_round = ""
        current_round_num = 0
        for line in debate_section.split("\n"):
            is_round_header = bool(_ROUND_HEADER_RE.search(line)) or any(
                m in line for m in round_markers_legacy
            )
            if is_round_header:
                if current_round.strip() and current_round_num > 0:
                    parts["rounds"].append(current_round.strip())
                current_round = line + "\n"
                current_round_num += 1
            else:
                current_round += line + "\n"

        if current_round.strip() and current_round_num > 0:
            parts["rounds"].append(current_round.strip())

        if not parts["rounds"]:
            parts["rounds"] = [debate_section]
    else:
        parts["header"] = report.strip()

    return parts


def hydrate_debate_from_report(full_report: str) -> dict | None:
    if not full_report or not full_report.strip():
        return None
    parts = parse_report_parts(full_report)
    if parts.get("rounds"):
        return {"rounds": parts["rounds"], "full": parts.get("full", full_report)}
    
    start = find_debate_start_index(full_report)
    if start is None:
        return None
    
    tail = full_report[start:]
    synth_hit = _find_first_marker(tail, _SYNTH_START_MARKERS)
    if synth_hit:
        section = tail[: synth_hit[0]].strip()
    else:
        disc_snip = "\n\n─────────────────────────"
        di = tail.find(disc_snip)
        section = tail[:di].strip() if di != -1 else tail.strip()
    
    if len(section) < 80:
        return None
    return {"rounds": [section], "full": full_report}


def build_short_report(parts: dict, stars: str, pct: int) -> list[str]:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    bull_summary = "Позиция бычья"
    bear_summary = "Позиция медвежья"

    if parts["rounds"]:
        round1 = parts["rounds"][0]
        lines = round1.split("\n")
        bull_lines, bear_lines = [], []
        in_bull = in_bear = False
        for line in lines:
            if _ROUND_HEADER_RE.search(line):
                in_bull = in_bear = False
                continue
            if "🐂 Bull" in line:
                in_bull, in_bear = True, False
                continue
            if "🐻 Bear" in line:
                in_bear, in_bull = True, False
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("──") or re.match(r"^[-_*═─]{3,}\s*$", stripped):
                continue
            if stripped.startswith("#") and len(stripped) < 80:
                continue
            if in_bull and len(bull_lines) < 3:
                bull_lines.append(stripped)
            elif in_bear and len(bear_lines) < 3:
                bear_lines.append(stripped)
        if bull_lines:
            bull_summary = strip_digest_summary_text("\n".join(bull_lines))
        if bear_lines:
            bear_summary = strip_digest_summary_text("\n".join(bear_lines))

    header = (
        f"📊 DIALECTIC EDGE — ЕЖЕДНЕВНЫЙ ДАЙДЖЕСТ\n"
        f"🕐 {now}\n\n"
        f"4 AI-модели изучили рынок и поспорили. Вот что вышло:\n\n"
        f"Уровень сигнала: {stars} ({pct}%)\n"
        f"{SIGNAL_PCT_EXPLAINED}\n\n"
        f"{'─' * 30}\n\n"
        f"🐂 Бычья позиция (кратко):\n{bull_summary}\n\n"
        f"🐻 Медвежья позиция (кратко):\n{bear_summary}\n\n"
        f"{'─' * 30}"
    )

    messages = [header]

    full = parts.get("full", "")
    synth_hit = _find_first_marker(full, _SYNTH_START_MARKERS)
    synth_start = synth_hit[0] if synth_hit else -1

    if synth_start != -1:
        synth_and_rest = full[synth_start:]
    else:
        synth_and_rest = parts.get("synthesis", "") + "\n\n" + parts.get("disclaimer", "")

    chunks = split_message(synth_and_rest, max_len=2500)
    for chunk in chunks:
        if chunk.strip():
            messages.append(chunk)

    return messages

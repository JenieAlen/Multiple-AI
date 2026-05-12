"""
LLM judge — picks the best answer among candidates.

Enhanced prompt with explicit scoring dimensions, anti-bias rules,
question-type awareness, and per-provider scores (1-10).
Falls back to the longest non-error answer if parsing fails.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from . import admin as _admin
from .providers import (
    GoogleProvider,
    GroqProvider,
    OllamaProvider,
    ProviderAnswer,
)


@dataclass
class JudgeVerdict:
    winner_provider: str
    winner_label: str
    rationale: str
    synthesized: Optional[str] = None
    judge_provider: str = ""
    scores: Optional[dict] = field(default=None)  # provider -> score 1-10


JUDGE_SYSTEM = (
    "You are an elite, impartial AI judge with deep expertise across all domains. "
    "Your task is to evaluate multiple AI assistant responses to the same question "
    "and determine which is best — then synthesize a superior answer from their combined strengths.\n\n"

    "EVALUATION DIMENSIONS (score each 1–10):\n"
    "1. Accuracy      — Factually correct? No hallucinations, no false claims.\n"
    "2. Completeness  — Fully addresses every part of the question?\n"
    "3. Clarity       — Well-structured, readable, easy to follow?\n"
    "4. Conciseness   — No filler, padding, or unnecessary repetition?\n"
    "5. Helpfulness   — Would a real user find this genuinely useful?\n\n"

    "JUDGING RULES:\n"
    "- Be completely objective. Ignore which AI model or provider gave the answer.\n"
    "- A short, precise answer beats a long, padded one. Do NOT favour length.\n"
    "- For technical questions (code, math, science): prioritise Accuracy above all.\n"
    "- For factual questions: prioritise Accuracy, then Completeness.\n"
    "- For creative or open-ended questions: prioritise Clarity and Helpfulness.\n"
    "- If answers are roughly equal, pick the most concise and direct one.\n"
    "- Your synthesized answer must be genuinely better than any single response — "
    "actively combine the best elements of all answers. "
    "Leave it empty ONLY if one answer is already near-perfect on all dimensions.\n\n"

    "OUTPUT — return ONLY this JSON with no extra text or markdown:\n"
    '{"winner": "<provider_name>", '
    '"scores": {"<provider_name>": <score_1_to_10>, ...}, '
    '"rationale": "<2-3 sentences: why the winner is best AND what the others lacked>", '
    '"synthesized": "<improved answer combining the best parts, or empty string>"}'
)


def _build_judge_prompt(question: str, answers: list[ProviderAnswer]) -> str:
    valid = [a for a in answers if not a.error]
    provider_list = ", ".join(a.provider for a in valid)

    parts = [
        f"QUESTION:\n{question}\n",
        f"Evaluate the {len(valid)} responses below independently, "
        "then choose the best or synthesize a superior answer.\n",
        "CANDIDATE RESPONSES:",
    ]
    for i, a in enumerate(valid, 1):
        parts.append(f"\n--- Response {i} | provider: {a.provider} | model: {a.model} ---")
        parts.append(a.answer)

    parts.append(
        f"\n---\nValid values for `winner` and `scores` keys: {provider_list}\n"
        "Return JSON only."
    )
    return "\n".join(parts)


def _extract_json(text: str) -> Optional[dict]:
    """Find the first JSON object in `text` and parse it."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _parse_scores(raw: object, valid_providers: list[str]) -> Optional[dict]:
    """Parse the scores dict; returns None if empty or unparseable."""
    if not isinstance(raw, dict) or not raw:
        return None
    out = {}
    for k, v in raw.items():
        key = str(k).strip().lower()
        if key not in valid_providers:
            continue
        try:
            score = max(1, min(10, int(float(v))))
            out[key] = score
        except (TypeError, ValueError):
            pass
    return out or None


def _fallback_verdict(answers: list[ProviderAnswer], reason: str) -> JudgeVerdict:
    valid = [a for a in answers if not a.error and a.answer]
    if not valid:
        return JudgeVerdict(
            winner_provider="",
            winner_label="",
            rationale="No usable answers were produced.",
        )
    best = max(valid, key=lambda a: len(a.answer))
    return JudgeVerdict(
        winner_provider=best.provider,
        winner_label=best.label,
        rationale=f"Fallback selection ({reason}): chose the longest non-error answer.",
    )


async def judge(question: str, answers: list[ProviderAnswer]) -> JudgeVerdict:
    valid = [a for a in answers if not a.error and a.answer]
    if not valid:
        return _fallback_verdict(answers, "no valid answers")
    if len(valid) == 1:
        only = valid[0]
        return JudgeVerdict(
            winner_provider=only.provider,
            winner_label=only.label,
            rationale="Only one provider returned a valid answer.",
            judge_provider="",
        )

    override = _admin.state.get_judge_override()
    judge_name = (override or os.getenv("JUDGE_PROVIDER", "google")).strip().lower()
    provider_map = {
        "groq": GroqProvider,
        "ollama": OllamaProvider,
        "google": GoogleProvider,
    }
    cls = provider_map.get(judge_name, GoogleProvider)
    judge_provider = cls()

    if not judge_provider.is_available():
        for alt_cls in provider_map.values():
            j = alt_cls()
            if j.is_available():
                judge_provider = j
                judge_name = j.name
                break

    if not judge_provider.is_available():
        return _fallback_verdict(answers, "no judge provider available")

    prompt = _build_judge_prompt(question, valid)
    result = await judge_provider.ask(prompt, system=JUDGE_SYSTEM, role="judge")
    if result.error:
        return _fallback_verdict(answers, f"judge error: {result.error}")

    parsed = _extract_json(result.answer)
    if not parsed or "winner" not in parsed:
        return _fallback_verdict(answers, "judge returned unparseable output")

    winner_name = str(parsed.get("winner", "")).strip().lower()
    winner = next((a for a in valid if a.provider == winner_name), None)
    if winner is None:
        return _fallback_verdict(answers, "judge picked an unknown provider")

    valid_names = [a.provider for a in valid]
    scores = _parse_scores(parsed.get("scores"), valid_names)
    synthesized = str(parsed.get("synthesized", "") or "").strip() or None

    return JudgeVerdict(
        winner_provider=winner.provider,
        winner_label=winner.label,
        rationale=str(parsed.get("rationale", "")).strip() or "(no rationale)",
        synthesized=synthesized,
        judge_provider=judge_name,
        scores=scores,
    )

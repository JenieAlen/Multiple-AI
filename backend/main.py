"""
FastAPI entry point for the Multi-AI app.

Endpoints:
  GET  /              -> serves the single-page UI
  GET  /api/health    -> reports which providers are configured
  POST /api/ask       -> fan out to all providers, run the judge, return result
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Load .env BEFORE importing modules that read os.getenv at import time.
# Only load .env (real secrets) — never .env.example (placeholders).
# On Render, .env won't exist and env vars come from the dashboard instead.
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_ROOT / ".env")

from . import admin as admin_state  # noqa: E402
from .judge import judge  # noqa: E402
from .providers import (  # noqa: E402
    GoogleProvider,
    GroqProvider,
    OpenRouterProvider,
    ask_all,
    build_providers,
)

# --------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

app = FastAPI(title="Multiple AI", version="1.0.0")


# ---- Schemas --------------------------------------------------------------
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=8000)


class AnswerOut(BaseModel):
    provider: str
    label: str
    model: str
    answer: str
    latency_ms: int
    error: Optional[str] = None
    is_winner: bool = False


class VerdictOut(BaseModel):
    winner_provider: str
    winner_label: str
    rationale: str
    synthesized: Optional[str] = None
    judge_provider: str
    scores: Optional[dict] = None


class AskResponse(BaseModel):
    question: str
    answers: list[AnswerOut]
    verdict: VerdictOut
    best_answer: str


# ---- API routes -----------------------------------------------------------
@app.get("/api/health")
async def health() -> dict:
    providers = build_providers()
    return {
        "ok": True,
        "providers": [
            {"name": p.name, "label": p.label, "model": p.model}
            for p in providers
        ],
        "configured_count": len(providers),
    }


@app.post("/api/ask", response_model=AskResponse)
async def ask(payload: AskRequest) -> AskResponse:
    question = payload.question.strip()
    if not question:
        raise HTTPException(400, "Question is empty.")

    answers = await ask_all(question)
    if not answers:
        raise HTTPException(
            503,
            "No AI providers are configured. Add API keys to your .env.example file.",
        )

    verdict = await judge(question, answers)

    answers_out = [
        AnswerOut(
            provider=a.provider,
            label=a.label,
            model=a.model,
            answer=a.answer,
            latency_ms=a.latency_ms,
            error=a.error,
            is_winner=(a.provider == verdict.winner_provider),
        )
        for a in answers
    ]

    # The "best answer" shown to the user: synthesized if available, otherwise
    # the winning provider's raw answer.
    winner_answer = next(
        (a.answer for a in answers if a.provider == verdict.winner_provider),
        "",
    )
    best = verdict.synthesized or winner_answer or "(no answer available)"

    return AskResponse(
        question=question,
        answers=answers_out,
        verdict=VerdictOut(
            winner_provider=verdict.winner_provider,
            winner_label=verdict.winner_label,
            rationale=verdict.rationale,
            synthesized=verdict.synthesized,
            judge_provider=verdict.judge_provider,
            scores=verdict.scores,
        ),
        best_answer=best,
    )


# ---- Admin schemas --------------------------------------------------------
class AdminLoginIn(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)


class AdminJudgeIn(BaseModel):
    provider: Optional[str] = None


class AdminProviderToggleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool


class AdminChangePasswordIn(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=6, max_length=256)


ADMIN_COOKIE = "ai_admin_session"

# All known provider classes, keyed by their `name`. Used by admin endpoints
# so we can describe providers even when they're not currently available.
_ALL_PROVIDER_CLASSES = (GroqProvider, OpenRouterProvider, GoogleProvider)


def _mask_key(key: str) -> str:
    """Show 8 fixed bullets + last 4 chars of an API key."""
    if not key:
        return ""
    return "••••••••" + key[-4:]


def require_admin(
    session: Optional[str] = Cookie(default=None, alias=ADMIN_COOKIE),
) -> str:
    """FastAPI dependency: 401 unless the request has a valid admin session."""
    if not admin_state.admin_enabled():
        raise HTTPException(503, "Admin is disabled (set ADMIN_PASSWORD in .env).")
    if not admin_state.state.is_session_valid(session):
        raise HTTPException(401, "Admin login required.")
    return session  # type: ignore[return-value]


# ---- Admin routes ---------------------------------------------------------
@app.post("/api/admin/login")
async def admin_login(payload: AdminLoginIn, response: Response) -> dict:
    if not admin_state.admin_enabled():
        raise HTTPException(503, "Admin is disabled (set ADMIN_PASSWORD in .env).")
    if not admin_state.check_password(payload.password):
        raise HTTPException(401, "Incorrect password.")
    token = admin_state.state.issue_session()
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        max_age=admin_state.SESSION_TTL_SECONDS,
        path="/",
    )
    return {"ok": True}


@app.post("/api/admin/logout")
async def admin_logout(
    response: Response,
    session: Optional[str] = Cookie(default=None, alias=ADMIN_COOKIE),
) -> dict:
    if session:
        admin_state.state.revoke_session(session)
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/admin/state")
async def admin_get_state(_: str = Depends(require_admin)) -> dict:
    """Return everything the admin UI needs to render."""
    # Build a row for every known provider (configured or not).
    disabled = admin_state.state.get_disabled_providers()
    providers_info = []
    for cls in _ALL_PROVIDER_CLASSES:
        instance = cls()
        providers_info.append(
            {
                "name": instance.name,
                "label": instance.label,
                "model": instance.model,
                "available": instance.is_available(),
                "enabled": instance.name not in disabled,
                "api_key_masked": _mask_key(instance.api_key),
            }
        )

    override = admin_state.state.get_judge_override()
    env_judge = override or os.getenv("JUDGE_PROVIDER", "google")
    return {
        "providers": providers_info,
        "judge": {
            "current": override or "",
            "env_default": env_judge,
            "available": [
                p["name"] for p in providers_info if p["available"]
            ],
        },
        "usage": {
            "totals": admin_state.state.usage_totals(),
            "recent": admin_state.state.list_usage(limit=100),
        },
    }


@app.post("/api/admin/judge")
async def admin_set_judge(
    payload: AdminJudgeIn,
    _: str = Depends(require_admin),
) -> dict:
    name = (payload.provider or "").strip().lower()
    if name in ("", "auto"):
        admin_state.state.set_judge_override(None)
        return {"ok": True, "judge": ""}
    valid = {cls().name for cls in _ALL_PROVIDER_CLASSES}
    if name not in valid:
        raise HTTPException(400, f"Unknown provider: {name}")
    admin_state.state.set_judge_override(name)
    return {"ok": True, "judge": name}


@app.post("/api/admin/provider-toggle")
async def admin_provider_toggle(
    payload: AdminProviderToggleIn,
    _: str = Depends(require_admin),
) -> dict:
    valid = {cls().name for cls in _ALL_PROVIDER_CLASSES}
    if payload.name not in valid:
        raise HTTPException(400, f"Unknown provider: {payload.name}")
    admin_state.state.set_provider_disabled(payload.name, not payload.enabled)
    return {"ok": True, "name": payload.name, "enabled": payload.enabled}


@app.post("/api/admin/clear-usage")
async def admin_clear_usage(_: str = Depends(require_admin)) -> dict:
    admin_state.state.clear_usage()
    return {"ok": True}


@app.post("/api/admin/change-password")
async def admin_change_password(
    payload: AdminChangePasswordIn,
    _: str = Depends(require_admin),
) -> dict:
    if not admin_state.check_password(payload.current_password):
        raise HTTPException(400, "Current password is incorrect.")
    os.environ["ADMIN_PASSWORD"] = payload.new_password
    return {"ok": True}


# ---- Static frontend ------------------------------------------------------
# Serve index.html at "/" and any static assets from /frontend.
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/admin")
async def admin_page() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "admin.html")


if FRONTEND_DIR.exists():
    app.mount(
        "/static",
        StaticFiles(directory=str(FRONTEND_DIR)),
        name="static",
    )

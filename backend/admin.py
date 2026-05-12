"""
Admin state (in-memory) for Multiple AI.

Holds:
  * the current judge-provider override (overrides JUDGE_PROVIDER env var)
  * a bounded log of recent provider calls (timestamp, provider, tokens, latency)
  * a simple session-token store for the admin UI

Everything here lives in process memory only and resets on restart. The
project's CLAUDE.md instructed "In-memory only" for storage of admin
settings/usage; nothing persists to disk.
"""

from __future__ import annotations

import os
import secrets
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Deque, Optional

# How many usage rows we keep in memory before evicting the oldest.
MAX_USAGE_ROWS = 500

# How long an admin session cookie is valid.
SESSION_TTL_SECONDS = 60 * 60 * 8  # 8 hours


@dataclass
class UsageRow:
    timestamp: float          # unix seconds
    provider: str             # e.g. "anthropic"
    label: str                # e.g. "Claude"
    model: str
    role: str                 # "ask" (user-facing call) or "judge"
    input_tokens: int
    output_tokens: int
    latency_ms: int
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


class _AdminState:
    """Thread-safe holder for runtime admin settings + usage log."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._judge_override: Optional[str] = None
        self._usage: Deque[UsageRow] = deque(maxlen=MAX_USAGE_ROWS)
        self._sessions: dict[str, float] = {}
        self._disabled_providers: set[str] = set()

    # ---- Judge override --------------------------------------------------
    def get_judge_override(self) -> Optional[str]:
        with self._lock:
            return self._judge_override

    def set_judge_override(self, name: Optional[str]) -> None:
        with self._lock:
            self._judge_override = name.lower() if name else None

    # ---- Usage log -------------------------------------------------------
    def record_usage(self, row: UsageRow) -> None:
        with self._lock:
            self._usage.append(row)

    def list_usage(self, limit: int = 100) -> list[dict]:
        with self._lock:
            # newest first
            rows = list(self._usage)[-limit:]
        rows.reverse()
        return [r.to_dict() for r in rows]

    def usage_totals(self) -> dict:
        """Aggregate totals per provider for the rows currently in memory."""
        with self._lock:
            rows = list(self._usage)
        totals: dict[str, dict] = {}
        for r in rows:
            t = totals.setdefault(
                r.provider,
                {
                    "provider": r.provider,
                    "label": r.label,
                    "calls": 0,
                    "errors": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                },
            )
            t["calls"] += 1
            if r.error:
                t["errors"] += 1
            t["input_tokens"] += r.input_tokens
            t["output_tokens"] += r.output_tokens
        return totals

    def clear_usage(self) -> None:
        with self._lock:
            self._usage.clear()

    # ---- Provider toggles ------------------------------------------------
    def set_provider_disabled(self, name: str, disabled: bool) -> None:
        with self._lock:
            if disabled:
                self._disabled_providers.add(name)
            else:
                self._disabled_providers.discard(name)

    def get_disabled_providers(self) -> set:
        with self._lock:
            return set(self._disabled_providers)

    def is_provider_disabled(self, name: str) -> bool:
        with self._lock:
            return name in self._disabled_providers

    # ---- Sessions --------------------------------------------------------
    def issue_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = time.time() + SESSION_TTL_SECONDS
            # Opportunistic cleanup of expired tokens.
            now = time.time()
            stale = [t for t, exp in self._sessions.items() if exp < now]
            for t in stale:
                self._sessions.pop(t, None)
        return token

    def revoke_session(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def is_session_valid(self, token: Optional[str]) -> bool:
        if not token:
            return False
        with self._lock:
            exp = self._sessions.get(token)
            if exp is None:
                return False
            if exp < time.time():
                self._sessions.pop(token, None)
                return False
            return True


# Module-level singleton.
state = _AdminState()


def check_password(submitted: str) -> bool:
    """Constant-time compare against ADMIN_PASSWORD env var."""
    expected = os.getenv("ADMIN_PASSWORD", "").strip()
    if not expected:
        # If no password is set, admin is disabled entirely.
        return False
    return secrets.compare_digest(submitted or "", expected)


def admin_enabled() -> bool:
    return bool(os.getenv("ADMIN_PASSWORD", "").strip())


def record_usage(
    *,
    provider: str,
    label: str,
    model: str,
    role: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    error: Optional[str] = None,
) -> None:
    """Convenience wrapper so callers don't have to import UsageRow."""
    state.record_usage(
        UsageRow(
            timestamp=time.time(),
            provider=provider,
            label=label,
            model=model,
            role=role,
            input_tokens=int(input_tokens or 0),
            output_tokens=int(output_tokens or 0),
            latency_ms=int(latency_ms or 0),
            error=error,
        )
    )

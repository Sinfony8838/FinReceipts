"""Runtime configuration, read from environment variables.

Secrets are never stored in config objects or files: we only keep the *name*
of the environment variable that holds an API key and resolve it at call time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SEC_USER_AGENT = "FinReceipts research bot contact@example.com"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    """Project settings. Construct with :meth:`from_env`."""

    sec_user_agent: str = DEFAULT_SEC_USER_AGENT
    cache_dir: Path = field(default_factory=lambda: Path(".cache/sec"))
    offline: bool = False
    llm_provider: str = "fake"
    llm_base_url: str = ""
    llm_api_key_env: str = ""
    small_model: str = ""
    large_model: str = ""
    allow_coding_plan: bool = False
    min_call_interval_s: float = 1.0

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            sec_user_agent=_env("FINRECEIPTS_SEC_USER_AGENT", DEFAULT_SEC_USER_AGENT),
            cache_dir=Path(_env("FINRECEIPTS_CACHE_DIR", ".cache/sec")),
            offline=_env("FINRECEIPTS_OFFLINE") in {"1", "true", "yes"},
            llm_provider=_env("FINRECEIPTS_LLM_PROVIDER", "fake"),
            llm_base_url=_env("FINRECEIPTS_LLM_BASE_URL"),
            llm_api_key_env=_env("FINRECEIPTS_LLM_API_KEY_ENV"),
            small_model=_env("FINRECEIPTS_SMALL_MODEL"),
            large_model=_env("FINRECEIPTS_LARGE_MODEL"),
            allow_coding_plan=_env("FINRECEIPTS_ALLOW_CODING_PLAN") in {"1", "true", "yes"},
            min_call_interval_s=float(_env("FINRECEIPTS_MIN_CALL_INTERVAL_S", "1.0")),
        )

    def resolve_api_key(self) -> str:
        """Return the API key from the configured env var (never logged)."""
        if not self.llm_api_key_env:
            raise RuntimeError("FINRECEIPTS_LLM_API_KEY_ENV is not set")
        key = os.environ.get(self.llm_api_key_env, "")
        if not key:
            raise RuntimeError(f"environment variable {self.llm_api_key_env} is empty")
        return key

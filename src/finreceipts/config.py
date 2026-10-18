"""Runtime configuration, read from environment variables.

Secrets are never stored in config objects or files: we only keep the *name*
of the environment variable that holds an API key and resolve it at call time.
"""

from __future__ import annotations

import math
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
    max_inflight: int = 4
    max_request_bytes: int = 262144
    request_read_timeout_s: float = 10.0
    response_timeout_s: float = 120.0

    def __post_init__(self):
        if type(self.max_inflight) is not int or not 1 <= self.max_inflight <= 32:
            raise ValueError("max_inflight must be an integer in [1, 32]")
        if (
            type(self.max_request_bytes) is not int
            or not 1024 <= self.max_request_bytes <= 16777216
        ):
            raise ValueError("max_request_bytes must be an integer in [1024, 16777216]")
        for name, maximum in (("request_read_timeout_s", 60), ("response_timeout_s", 600)):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"{name} must be finite and in (0, {maximum}]")

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
            max_inflight=int(_env("FINRECEIPTS_MAX_INFLIGHT", "4")),
            max_request_bytes=int(_env("FINRECEIPTS_MAX_REQUEST_BYTES", "262144")),
            request_read_timeout_s=float(_env("FINRECEIPTS_REQUEST_READ_TIMEOUT_S", "10")),
            response_timeout_s=float(_env("FINRECEIPTS_RESPONSE_TIMEOUT_S", "120")),
        )

    def resolve_api_key(self) -> str:
        """Return the API key from the configured env var (never logged)."""
        if not self.llm_api_key_env:
            raise RuntimeError("FINRECEIPTS_LLM_API_KEY_ENV is not set")
        key = os.environ.get(self.llm_api_key_env, "")
        if not key:
            raise RuntimeError(f"environment variable {self.llm_api_key_env} is empty")
        return key

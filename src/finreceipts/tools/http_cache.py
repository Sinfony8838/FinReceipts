"""Disk cache with record/replay semantics for HTTP GET JSON responses.

Modes
-----
``auto``    use the cache if present, otherwise fetch and record.
``replay``  never touch the network; a cache miss is an error (used in CI).
``refresh`` always fetch and overwrite the cache.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

Mode = Literal["auto", "replay", "refresh"]


class CacheMiss(LookupError):
    """Raised in ``replay`` mode when a response was never recorded."""


def cache_key(url: str) -> str:
    """Stable, human-skimmable file name for a URL."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", url.split("://", 1)[-1]).strip("_")[-80:]
    digest = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"{slug}__{digest}.json.gz"


class JsonCache:
    """Gzip-compressed JSON files keyed by URL. Stores only response bodies."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, url: str) -> Path:
        return self.root / cache_key(url)

    def get(self, url: str) -> Any | None:
        path = self.path_for(url)
        if not path.exists():
            return None
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def put(self, url: str, payload: Any) -> Path:
        path = self.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        return path

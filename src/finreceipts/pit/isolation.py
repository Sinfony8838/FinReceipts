"""Policy/catalog scoped envelopes for host caches and checkpoints.

No persistence or deserialization of executable objects. This is an integrity
boundary for trusted storage, not authentication against a malicious storage writer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .policy import Catalog, Policy, content_hash, digest


def namespace(policy: Policy, catalog: Catalog) -> str:
    return digest(
        {"schema": "pit-envelope-v1", "policy": policy.fingerprint, "catalog": catalog.fingerprint}
    )


def cache_key(policy: Policy, catalog: Catalog, request: dict) -> str:
    return digest({"namespace": namespace(policy, catalog), "request": request})


@dataclass(frozen=True)
class Envelope:
    scope: str
    purpose: str
    payload_json: str
    sha256: str

    @classmethod
    def seal(cls, policy: Policy, catalog: Catalog, purpose: str, payload: dict) -> Envelope:
        if not purpose:
            raise ValueError("cache/checkpoint purpose is required")
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return cls(namespace(policy, catalog), purpose, raw, content_hash(raw.encode()))

    def open(self, policy: Policy, catalog: Catalog, purpose: str) -> dict:
        if self.scope != namespace(policy, catalog) or self.purpose != purpose:
            raise ValueError("cache/checkpoint policy, evidence catalog or purpose mismatch")
        if content_hash(self.payload_json.encode()) != self.sha256:
            raise ValueError("cache/checkpoint integrity mismatch")
        payload = json.loads(self.payload_json)
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload

"""Explicit local identity or server-configured bearer-token identities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass, field

from fastapi import Request
from starlette.responses import JSONResponse

from finreceipts.app.diagnostics import error_payload


@dataclass(frozen=True)
class AuthPolicy:
    mode: str = "local"
    token_hashes: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    @classmethod
    def from_env(cls):
        mode = os.environ.get("FINRECEIPTS_AUTH_MODE", "local").strip()
        if mode == "local":
            return cls()
        if mode != "token":
            raise ValueError("FINRECEIPTS_AUTH_MODE must be local or token")
        name = os.environ.get("FINRECEIPTS_AUTH_TOKENS_ENV", "").strip()
        try:
            raw = os.environ.get(name, "")
            if not raw.lstrip().startswith("{"):
                raise ValueError
            pairs = json.loads(raw, object_pairs_hook=list)
            if not isinstance(pairs, list) or not pairs:
                raise ValueError
            users, hashes = set(), set()
            for user, token_hash in pairs:
                if (
                    not isinstance(user, str)
                    or user == "local"
                    or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", user)
                    or not isinstance(token_hash, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", token_hash)
                    or user in users
                    or token_hash in hashes
                ):
                    raise ValueError
                users.add(user)
                hashes.add(token_hash)
        except (ValueError, TypeError):
            raise ValueError("invalid authentication digest mapping") from None
        return cls(mode, tuple(pairs))

    def identify(self, authorization: str) -> str | None:
        if self.mode == "local":
            return "local"
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token or len(token) > 1024:
            return None
        supplied = hashlib.sha256(token.encode("utf-8")).hexdigest()
        for user, expected in self.token_hashes:
            if secrets.compare_digest(supplied, expected):
                return user
        return None


class Authentication:
    def __init__(self, app, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        values = [v for k, v in scope["headers"] if k.lower() == b"authorization"]
        authorization = values[0].decode("latin-1") if len(values) == 1 else ""
        principal = self.state.auth_policy.identify(authorization)
        if principal is None:
            response = JSONResponse(
                error_payload(Request(scope), "authentication_required"),
                status_code=401,
                headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
            )
            return await response(scope, receive, send)
        scope.setdefault("state", {})["principal_id"] = principal

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower() not in {b"cache-control", b"vary"}
                ]
                headers += [(b"cache-control", b"no-store"), (b"vary", b"Authorization")]
                message = {**message, "headers": headers}
            await send(message)

        return await self.app(scope, receive, private_send)

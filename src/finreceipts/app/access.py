"""Host and browser-origin checks for a local, single-trust-domain workbench.

These headers can be forged by non-browser clients: this is not authentication.
"""

from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse

from finreceipts.app.diagnostics import error_payload


def _authority(value: str, scheme: str) -> tuple[str, int]:
    if not value or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        raise ValueError("invalid authority")
    if any(char in value for char in "/\\?#@%,") or value.endswith(":"):
        raise ValueError("invalid authority")
    parsed = urlsplit(f"{scheme}://{value}")
    host = parsed.hostname
    if not host or scheme not in {"http", "https"}:
        raise ValueError("invalid authority")
    if ":" in host:
        host = str(ipaddress.IPv6Address(host))
    elif not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
        raise ValueError("invalid hostname")
    if any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        for label in host.split(".")
    ):
        raise ValueError("invalid hostname")
    port = parsed.port or (443 if scheme == "https" else 80)
    if parsed.port == 0:
        raise ValueError("invalid port")
    return host, port


def _origin(value: str, *, referer=False) -> tuple[str, str, int]:
    if not value or any(ord(char) <= 32 or ord(char) >= 127 for char in value):
        raise ValueError("invalid origin")
    parsed = urlsplit(value)
    if not referer and value != f"{parsed.scheme}://{parsed.netloc}":
        raise ValueError("origin must not contain a path")
    host, port = _authority(parsed.netloc, parsed.scheme)
    return parsed.scheme, host, port


@dataclass(frozen=True)
class AccessPolicy:
    hosts: frozenset[str]

    @classmethod
    def from_env(cls):
        values = os.environ.get("FINRECEIPTS_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
        hosts = set()
        for raw in values:
            value = raw.strip()
            # Configuration names hosts, not URLs, ports, suffixes or wildcard rules.
            if ":" in value and not (value.startswith("[") and value.endswith("]")):
                raise ValueError("FINRECEIPTS_ALLOWED_HOSTS requires exact hosts without ports")
            host, _ = _authority(value, "http")
            hosts.add(host)
        return cls(frozenset(hosts))


def _private(scope) -> bool:
    path = scope["path"]
    root = scope.get("root_path", "").rstrip("/")
    if root and path.startswith(root + "/"):
        path = path[len(root) :]
    return path == "/api" or path.startswith("/api/") or path.rstrip("/") == "/health"


class SecurityHeaders:
    """Outside diagnostics so sanitized unexpected errors retain the same policy."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        private = _private(scope)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                extra = {
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"content-security-policy": b"frame-ancestors 'none'",
                    b"referrer-policy": b"no-referrer",
                    b"cross-origin-resource-policy": b"same-origin",
                }
                if private:
                    extra[b"cache-control"] = b"no-store"
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() not in extra]
                message = {**message, "headers": headers + list(extra.items())}
            await send(message)

        await self.app(scope, receive, secured_send)


class BrowserBoundary:
    def __init__(self, app, *, state):
        self.app, self.state = app, state

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {}
        duplicates = set()
        for name, value in scope.get("headers", []):
            name = name.lower()
            if name in headers:
                duplicates.add(name)
            headers[name] = value.decode("latin-1")
        code, status = None, 403
        try:
            if b"host" in duplicates:
                raise ValueError("duplicate Host")
            host, port = _authority(headers.get(b"host", ""), scope["scheme"])
            if host not in self.state.access_policy.hosts:
                raise ValueError("untrusted host")
        except ValueError:
            code, status = "untrusted_host", 400
        if code is None and _private(scope):
            expected = (scope["scheme"], host, port)
            try:
                if duplicates & {b"origin", b"referer", b"sec-fetch-site"}:
                    raise ValueError("ambiguous browser headers")
                site = headers.get(b"sec-fetch-site")
                if site is not None and site not in {"same-origin", "none"}:
                    raise ValueError("cross-origin fetch")
                if b"origin" in headers and _origin(headers[b"origin"]) != expected:
                    raise ValueError("cross-origin request")
                if (
                    b"origin" not in headers
                    and b"referer" in headers
                    and _origin(headers[b"referer"], referer=True) != expected
                ):
                    raise ValueError("cross-origin referer")
            except ValueError:
                code = "cross_origin_denied"
        if code:
            await JSONResponse(error_payload(Request(scope), code), status_code=status)(
                scope, receive, send
            )
            return
        await self.app(scope, receive, send)

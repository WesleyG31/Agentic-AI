"""OIDC discovery, JWKS signature verification, and ingress identity context.

The verifier accepts access tokens only after deterministic cryptographic and claim
validation.  Discovery/JWKS locations are administrator configuration, never request data.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic
from typing import Any
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWK

from kompass.security.identity import ClaimsIdentityAdapter, Principal


class OIDCAuthenticationError(ValueError):
    """A public, non-sensitive authentication failure."""


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    audience: str
    discovery_url: str = ""
    jwks_url: str = ""
    timeout_seconds: float = 5.0
    clock_skew_seconds: int = 30
    cache_seconds: float = 300.0
    allow_http: bool = False

    def __post_init__(self) -> None:
        if not self.issuer or not self.audience:
            raise ValueError("OIDC issuer and audience are required")
        if self.timeout_seconds <= 0 or self.clock_skew_seconds < 0:
            raise ValueError("invalid OIDC timeout or clock skew")
        for url in filter(None, (self.issuer, self.discovery_url, self.jwks_url)):
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("OIDC endpoints must be absolute HTTP(S) URLs")
            if parsed.scheme != "https" and not self.allow_http:
                raise ValueError("OIDC endpoints require HTTPS outside local development")


class OIDCVerifier:
    """Cached asynchronous verifier for RS256 access tokens."""

    def __init__(
        self,
        config: OIDCConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._configuration: Mapping[str, Any] | None = None
        self._jwks: Mapping[str, Any] | None = None
        self._cache_deadline = 0.0
        self._lock = asyncio.Lock()

    async def verify(self, token: str) -> Principal:
        if not token or len(token) > 16_384:
            raise OIDCAuthenticationError("invalid bearer token")
        configuration, jwks = await self._metadata()
        try:
            header = jwt.get_unverified_header(token)
            kid = str(header.get("kid", ""))
            algorithm = str(header.get("alg", ""))
            supported = configuration.get("id_token_signing_alg_values_supported", ["RS256"])
            if algorithm != "RS256" or algorithm not in supported or not kid:
                raise OIDCAuthenticationError("unsupported token signing key")
            candidates = [key for key in jwks.get("keys", ()) if str(key.get("kid")) == kid]
            if len(candidates) != 1:
                # A key rotation may have occurred inside the cache window.
                self._cache_deadline = 0.0
                configuration, jwks = await self._metadata()
                candidates = [
                    key for key in jwks.get("keys", ()) if str(key.get("kid")) == kid
                ]
            if len(candidates) != 1:
                raise OIDCAuthenticationError("unknown token signing key")
            public_key = PyJWK.from_dict(candidates[0], algorithm="RS256").key
            claims = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.config.audience,
                issuer=self.config.issuer.rstrip("/"),
                leeway=self.config.clock_skew_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            principal = ClaimsIdentityAdapter().from_verified_claims(claims)
        except OIDCAuthenticationError:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise OIDCAuthenticationError("invalid bearer token") from exc
        return principal

    async def _metadata(self) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
        if (
            self._configuration is not None
            and self._jwks is not None
            and monotonic() < self._cache_deadline
        ):
            return self._configuration, self._jwks
        async with self._lock:
            if (
                self._configuration is not None
                and self._jwks is not None
                and monotonic() < self._cache_deadline
            ):
                return self._configuration, self._jwks
            discovery_url = self.config.discovery_url or (
                f"{self.config.issuer.rstrip('/')}/.well-known/openid-configuration"
            )
            configuration = await self._get_json(discovery_url)
            if str(configuration.get("issuer", "")).rstrip("/") != self.config.issuer.rstrip(
                "/"
            ):
                raise OIDCAuthenticationError("OIDC discovery issuer mismatch")
            jwks_url = self.config.jwks_url or str(configuration.get("jwks_uri", ""))
            if not jwks_url:
                raise OIDCAuthenticationError("OIDC discovery omitted JWKS URI")
            jwks = await self._get_json(jwks_url)
            if not isinstance(jwks.get("keys"), list):
                raise OIDCAuthenticationError("invalid OIDC JWKS document")
            self._configuration = configuration
            self._jwks = jwks
            self._cache_deadline = monotonic() + self.config.cache_seconds
            return configuration, jwks

    async def _get_json(self, url: str) -> Mapping[str, Any]:
        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCAuthenticationError("OIDC metadata is unavailable") from exc
        if not isinstance(data, Mapping):
            raise OIDCAuthenticationError("invalid OIDC metadata document")
        return data


_VERIFIED_PRINCIPAL: ContextVar[Principal | None] = ContextVar(
    "kompass_verified_principal", default=None
)


def get_verified_principal() -> Principal | None:
    return _VERIFIED_PRINCIPAL.get()


@asynccontextmanager
async def verified_principal_scope(principal: Principal) -> AsyncIterator[None]:
    token = _VERIFIED_PRINCIPAL.set(principal)
    try:
        yield
    finally:
        _VERIFIED_PRINCIPAL.reset(token)

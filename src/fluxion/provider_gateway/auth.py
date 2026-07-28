"""Local access control for the Provider Gateway.

The gateway holds every upstream credential the user has configured, so "it only
listens on loopback" is not by itself a security boundary: any process on the
machine can reach loopback. It carries its own bearer token, separate from the
web UI's, and refuses to bind anywhere else without TLS.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# 32 bytes of urlsafe base64. Long enough that guessing is not a threat model.
_TOKEN_BYTES = 32

# Owner read/write only. A token another local user can read is not a token.
_TOKEN_FILE_MODE = 0o600

_BEARER_PREFIX = "bearer "


class AuthError(Exception):
    """Request carried no valid credential."""


class InsecureBindError(RuntimeError):
    """Refusing to start with a configuration that would expose credentials."""


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def load_or_create_token(path: Path | str) -> str:
    """Read the gateway token, creating it on first run.

    Created with restrictive permissions from the start rather than chmod-ed
    afterwards: a token that is briefly world-readable has already leaked on a
    shared machine.
    """
    token_path = Path(path)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            _warn_if_permissive(token_path)
            return token

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token = generate_token()
    # Open with the mode applied at creation time, so there is no window in
    # which the file exists with default permissions.
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _TOKEN_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def _warn_if_permissive(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        log.warning(
            "provider gateway token at %s is readable by other users (mode %o); tighten it to 600",
            path,
            stat.S_IMODE(mode),
        )


@dataclass(frozen=True)
class TokenAuthenticator:
    """Validates the gateway's own bearer token."""

    token: str

    def __post_init__(self) -> None:
        if not self.token:
            raise ValueError("gateway token must not be empty")

    def verify_header(self, authorization: str | None) -> None:
        """Raise `AuthError` unless the header carries our token.

        Comparison is constant-time. A naive `==` leaks the token's prefix
        through timing, and this endpoint is reachable by any local process, so
        the attack is not theoretical.
        """
        if not authorization:
            raise AuthError("missing Authorization header")
        if not authorization.lower().startswith(_BEARER_PREFIX):
            raise AuthError("Authorization header must use the Bearer scheme")
        presented = authorization[len(_BEARER_PREFIX) :].strip()
        if not hmac.compare_digest(presented, self.token):
            raise AuthError("invalid gateway token")


def is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_bind(host: str, *, tls_enabled: bool = False, token: str | None = None) -> None:
    """Refuse a bind that would expose upstream credentials.

    Non-loopback binding is not forbidden outright — someone may genuinely want
    a shared gateway — but it must be deliberate, encrypted, and authenticated.
    Failing at startup is the only reliable place to enforce that; a warning in
    a log nobody reads is not a control.
    """
    if is_loopback(host):
        return
    problems = []
    if not tls_enabled:
        problems.append("TLS is not enabled")
    if not token:
        problems.append("no gateway token is configured")
    if problems:
        raise InsecureBindError(
            f"refusing to bind the provider gateway to {host!r}: {', '.join(problems)}. "
            "The gateway can reach every configured upstream credential; bind to "
            "127.0.0.1, or enable TLS and set a token."
        )

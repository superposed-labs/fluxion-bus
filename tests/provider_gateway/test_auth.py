from __future__ import annotations

import stat

import pytest

from fluxion.provider_gateway.auth import (
    AuthError,
    InsecureBindError,
    TokenAuthenticator,
    check_bind,
    generate_token,
    is_loopback,
    load_or_create_token,
)


def test_generated_tokens_are_unique_and_long():
    tokens = {generate_token() for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(token) >= 32 for token in tokens)


def test_token_file_is_created_owner_only(tmp_path):
    """A token that is briefly world-readable has already leaked."""
    path = tmp_path / "nested" / "provider.token"
    token = load_or_create_token(path)
    assert token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_token_is_reused(tmp_path):
    path = tmp_path / "provider.token"
    first = load_or_create_token(path)
    assert load_or_create_token(path) == first


def test_blank_token_file_is_regenerated(tmp_path):
    path = tmp_path / "provider.token"
    path.write_text("   \n")
    assert load_or_create_token(path).strip()


def test_permissive_token_file_warns(tmp_path, caplog):
    path = tmp_path / "provider.token"
    load_or_create_token(path)
    path.chmod(0o644)
    with caplog.at_level("WARNING"):
        load_or_create_token(path)
    assert "readable by other users" in caplog.text


def test_valid_token_is_accepted():
    TokenAuthenticator("secret").verify_header("Bearer secret")


def test_bearer_scheme_is_case_insensitive():
    TokenAuthenticator("secret").verify_header("bearer secret")


@pytest.mark.parametrize(
    ("header", "match"),
    [
        (None, "missing"),
        ("", "missing"),
        ("secret", "Bearer scheme"),
        ("Basic secret", "Bearer scheme"),
        ("Bearer wrong", "invalid"),
        ("Bearer ", "invalid"),
        # A prefix match must fail: this is what constant-time comparison buys.
        ("Bearer sec", "invalid"),
    ],
)
def test_bad_credentials_are_rejected(header, match):
    with pytest.raises(AuthError, match=match):
        TokenAuthenticator("secret").verify_header(header)


def test_empty_token_is_rejected_at_construction():
    """An authenticator with no token would accept "Bearer "."""
    with pytest.raises(ValueError):
        TokenAuthenticator("")


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", ""])
def test_loopback_hosts_are_recognized(host):
    assert is_loopback(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com", "::"])
def test_non_loopback_hosts_are_recognized(host):
    assert not is_loopback(host)


def test_loopback_bind_needs_nothing_extra():
    check_bind("127.0.0.1")


def test_non_loopback_bind_without_tls_is_refused():
    """The gateway can reach every configured upstream credential."""
    with pytest.raises(InsecureBindError, match="TLS is not enabled"):
        check_bind("0.0.0.0", token="t")


def test_non_loopback_bind_without_a_token_is_refused():
    with pytest.raises(InsecureBindError, match="no gateway token"):
        check_bind("0.0.0.0", tls_enabled=True)


def test_non_loopback_bind_lists_every_problem():
    with pytest.raises(InsecureBindError) as excinfo:
        check_bind("0.0.0.0")
    message = str(excinfo.value)
    assert "TLS is not enabled" in message
    assert "no gateway token" in message


def test_deliberate_non_loopback_bind_is_allowed():
    check_bind("0.0.0.0", tls_enabled=True, token="t")

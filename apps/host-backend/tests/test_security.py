"""Tests for password hashing, JWTs, and PKCE."""

from __future__ import annotations

import time

import jwt
import pytest

from app.core.security import (
    code_challenge_from_verifier,
    create_token,
    decode_token,
    generate_code_verifier,
    hash_password,
    sha256_hex,
    verify_code_challenge,
    verify_password,
)


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_password_hashes_are_salted():
    """Two hashes of the same password must differ."""
    assert hash_password("same") != hash_password("same")


def test_access_token_roundtrip():
    token = create_token("user-123", "access", scopes=["chat"])
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "user-123"
    assert payload["scope"] == "chat"


def test_token_type_is_enforced():
    """A refresh token must not be accepted where an access token is required."""
    refresh = create_token("user-123", "refresh")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(refresh, expected_type="access")


def test_audience_is_enforced():
    """A token minted for one resource must not validate for another."""
    token = create_token("user-1", "access", audience="https://mcp-a.example/mcp")
    decode_token(token, audience="https://mcp-a.example/mcp")

    with pytest.raises(jwt.InvalidAudienceError):
        decode_token(token, audience="https://mcp-b.example/mcp")


def test_expired_token_is_rejected():
    token = create_token("user-1", "access", ttl_seconds=1)
    time.sleep(1.1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token)


def test_token_signed_with_another_key_is_rejected():
    forged = jwt.encode({"sub": "attacker"}, "some-other-key", algorithm="HS256")
    with pytest.raises(jwt.InvalidTokenError):
        decode_token(forged)


def test_pkce_challenge_verification():
    verifier = generate_code_verifier()
    challenge = code_challenge_from_verifier(verifier)

    assert verify_code_challenge(verifier, challenge)
    assert not verify_code_challenge("a-different-verifier", challenge)


def test_pkce_challenge_is_deterministic():
    verifier = generate_code_verifier()
    assert code_challenge_from_verifier(verifier) == code_challenge_from_verifier(verifier)


def test_sha256_hex_is_stable():
    assert sha256_hex("token") == sha256_hex("token")
    assert sha256_hex("token") != sha256_hex("token2")
    assert len(sha256_hex("token")) == 64

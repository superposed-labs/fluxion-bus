"""Ed25519 signing/verification for QQ webhook callbacks.

QQ derives an Ed25519 key pair from the bot's ``AppSecret``: the secret string is
repeated until it is at least 32 bytes, then truncated to exactly 32 bytes to use
as the private-key seed. Two operations are needed:

* **Callback validation** (``op:13``): when a webhook URL is configured, QQ sends
  a ``plain_token`` + ``event_ts`` and expects the hex Ed25519 signature over
  ``event_ts + plain_token`` so it can confirm we hold the secret.
* **Inbound verification**: every pushed event carries ``X-Signature-Ed25519`` and
  ``X-Signature-Timestamp`` headers; the signature covers ``timestamp + body`` and
  is verified against the public key derived from the same seed, proving the
  request really came from QQ.

Uses the ``cryptography`` package (already a transitive dependency) rather than
PyNaCl, so no new requirement is added.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _seed_from_secret(client_secret: str) -> bytes:
    """Expand/truncate the bot secret into the 32-byte Ed25519 seed QQ expects."""
    seed = client_secret
    while len(seed.encode("utf-8")) < 32:
        seed += seed
    return seed.encode("utf-8")[:32]


def callback_signature(client_secret: str, event_ts: str, plain_token: str) -> str:
    """Return the hex Ed25519 signature for an ``op:13`` callback validation."""
    private_key = Ed25519PrivateKey.from_private_bytes(_seed_from_secret(client_secret))
    message = (event_ts + plain_token).encode("utf-8")
    return private_key.sign(message).hex()


def verify_signature(client_secret: str, timestamp: str, body: bytes, signature_hex: str) -> bool:
    """Verify a pushed event's ``X-Signature-Ed25519`` header over ``timestamp + body``."""
    if not signature_hex or not timestamp:
        return False
    try:
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False
    public_key = Ed25519PrivateKey.from_private_bytes(_seed_from_secret(client_secret)).public_key()
    message = timestamp.encode("utf-8") + body
    try:
        public_key.verify(signature, message)
        return True
    except InvalidSignature:
        return False

"""AES-128-ECB cryptography helper functions for WeChat iLink.

All media files exchanged through WeChat iLink are encrypted/decrypted using
AES-128-ECB with PKCS7 padding.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def decode_aes_key(key_str: str, key_is_hex: bool = False) -> bytes:
    """Decode a base64-encoded iLink key to raw bytes.

    Supports both base64(raw 16 bytes) and base64(hex string of 16 bytes)
    via automatic length-based detection.
    """
    raw = base64.b64decode(key_str.strip())
    if len(raw) == 32:
        try:
            return bytes.fromhex(raw.decode("ascii"))
        except Exception:
            pass
    if key_is_hex:
        try:
            return bytes.fromhex(raw.decode("ascii"))
        except Exception:
            pass
    return raw


def encode_aes_key(key_bytes: bytes, key_is_hex: bool) -> str:
    """Encode raw bytes to a base64-encoded iLink key string."""
    if key_is_hex:
        hex_str = key_bytes.hex().encode("ascii")
        return base64.b64encode(hex_str).decode("ascii")
    return base64.b64encode(key_bytes).decode("ascii")


def encrypt_aes_ecb(data: bytes, key: bytes) -> bytes:
    """Encrypt data using AES-128-ECB with PKCS7 padding."""
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(data) + padder.finalize()

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded_data) + encryptor.finalize()


def decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt data using AES-128-ECB and strip PKCS7 padding."""
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    decrypted_data = decryptor.update(ciphertext) + decryptor.finalize()

    unpadder = padding.PKCS7(128).unpadder()
    return unpadder.update(decrypted_data) + unpadder.finalize()

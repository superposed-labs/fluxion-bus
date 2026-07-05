from fluxion.channels.wechat.crypto import (
    decode_aes_key,
    decrypt_aes_ecb,
    encode_aes_key,
    encrypt_aes_ecb,
)


def test_aes_key_encoding_raw():
    # Test raw base64 key encoding/decoding (used for images)
    raw_key = b"1234567890abcdef"  # 16 bytes
    assert len(raw_key) == 16

    encoded = encode_aes_key(raw_key, key_is_hex=False)
    # base64.b64encode(b"1234567890abcdef") -> 'MTIzNDU2Nzg5MGFiY2RlZg=='
    assert encoded == "MTIzNDU2Nzg5MGFiY2RlZg=="

    decoded = decode_aes_key(encoded, key_is_hex=False)
    assert decoded == raw_key


def test_aes_key_encoding_hex():
    # Test base64 of hex string encoding/decoding (used for files/voice/video)
    raw_key = b"1234567890abcdef"  # 16 bytes
    assert len(raw_key) == 16

    encoded = encode_aes_key(raw_key, key_is_hex=True)
    # raw_key.hex() -> '31323334353637383930616263646566'
    # base64.b64encode(b'31323334353637383930616263646566') -> 'MzEzMjMzMzQzNTM2MzczODM5MzA2MTYyNjM2NDY1NjY='
    assert encoded == "MzEzMjMzMzQzNTM2MzczODM5MzA2MTYyNjM2NDY1NjY="

    decoded = decode_aes_key(encoded, key_is_hex=True)
    assert decoded == raw_key


def test_encrypt_decrypt_ecb():
    key = b"supersecretkey12"  # 16 bytes

    # Test various lengths to verify padding
    payloads = [
        b"",
        b"hello",
        b"A" * 15,
        b"A" * 16,
        b"A" * 17,
        b"A" * 100,
    ]

    for payload in payloads:
        ciphertext = encrypt_aes_ecb(payload, key)
        # Ciphertext length must be a multiple of 16 and greater than payload length
        assert len(ciphertext) % 16 == 0
        assert len(ciphertext) > len(payload)

        decrypted = decrypt_aes_ecb(ciphertext, key)
        assert decrypted == payload

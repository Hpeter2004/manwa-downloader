"""AES-256-CBC decryption for encrypted manga images.

The manwaqi.cc site encrypts images served from some CDN servers using
AES-256-CBC. The decryption key and IV extraction logic is derived from
the site's base.js (BaseUtil.getSecureImageUrl).

Encrypted data layout:
    bytes 0-15  : IV (initialization vector)
    bytes 16+   : AES-256-CBC ciphertext

Key: "0B6666A0-BB59-1381-B746-a0E4C9AC" (UTF-8 encoded, truncated to 32 bytes)
"""

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

# AES-256 key from the website's JavaScript (base.js)
_AES_KEY_STR = "0B6666A0-BB59-1381-B746-a0E4C9AC"
_AES_KEY = _AES_KEY_STR.encode("utf-8")[:32]  # 32 bytes for AES-256


# Known image magic bytes (first bytes of standard image formats)
_IMAGE_MAGIC = {
    b"\xff\xd8\xff": ".jpg",       # JPEG
    b"\x89PNG": ".png",            # PNG
    b"GIF8": ".gif",               # GIF
    b"RIFF": ".webp",              # WebP
    b"\x00\x00\x01\x00": ".ico",   # ICO
    b"BM": ".bmp",                 # BMP
}

# Content-Type to extension mapping
_CT_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "image/avif": ".avif",
}


def is_encrypted(data: bytes) -> bool:
    """Check if image data is AES-encrypted (no standard image magic bytes)."""
    if len(data) < 4:
        return True  # Too short to determine, treat as problematic
    for magic in _IMAGE_MAGIC:
        if data.startswith(magic):
            return False
    return True


def get_ext_from_magic(data: bytes) -> str | None:
    """Determine file extension from magic bytes. Returns None if unknown."""
    if len(data) < 4:
        return None
    for magic, ext in _IMAGE_MAGIC.items():
        if data.startswith(magic):
            return ext
    return None


def get_ext_from_content_type(ct: str) -> str | None:
    """Determine file extension from HTTP Content-Type header."""
    if not ct:
        return None
    ct = ct.split(";")[0].strip().lower()
    return _CT_MAP.get(ct)


def decrypt_image(data: bytes) -> bytes | None:
    """Decrypt AES-256-CBC encrypted image data.

    Args:
        data: Raw bytes from CDN (IV[0:16] + ciphertext[16:])

    Returns:
        Decrypted bytes, or None if decryption fails.
    """
    if len(data) < 32:
        return None  # Need at least 16 bytes IV + 16 bytes ciphertext

    try:
        iv = data[:16]
        ciphertext = data[16:]

        cipher = AES.new(_AES_KEY, AES.MODE_CBC, iv=iv)
        plaintext = cipher.decrypt(ciphertext)

        # Remove PKCS7 padding
        plaintext = unpad(plaintext, AES.block_size)
        return plaintext
    except (ValueError, KeyError, Exception):
        return None


def try_decrypt_if_needed(data: bytes) -> tuple[bytes, bool]:
    """Check if data is encrypted, and decrypt if so.

    Returns:
        (processed_bytes, was_decrypted)
    """
    if is_encrypted(data):
        decrypted = decrypt_image(data)
        if decrypted is not None:
            return decrypted, True
    return data, False

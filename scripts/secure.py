"""홈페이지 데이터 잠금 — SITE_PASSWORD(GitHub Secrets)로 data.json을 암호화한다.

파일 형식 data.bin:
  b"TRE1" | 반복횟수(4바이트, big-endian) | IV 12바이트 | AES-256-GCM 암호문(gzip으로 줄인 JSON)
키 = PBKDF2-SHA256(비밀번호, 고정 소금 SALT, 반복횟수) — 브라우저의 WebCrypto로 같은 키를 만들어 푼다.
소금이 고정이라 '이 기기에서 기억'으로 저장한 키가 다음 갱신 뒤에도 그대로 맞는다.
"""
import gzip
import hashlib
import os
import struct

SALT = b"trend-radar/yuiop3245/v1"
ITER = 310000
MAGIC = b"TRE1"


def password():
    return (os.environ.get("SITE_PASSWORD") or "").strip()


def key_of(pw: str) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), SALT, ITER, 32)


def seal(raw: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    iv = os.urandom(12)
    return MAGIC + struct.pack(">I", ITER) + iv + AESGCM(key).encrypt(iv, gzip.compress(raw, 9), None)


def open_(blob: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    assert blob[:4] == MAGIC
    iv = blob[8:20]
    return gzip.decompress(AESGCM(key).decrypt(iv, blob[20:], None))

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - Secure Configuration & DPAPI Credential Protection
Provides Windows DPAPI-based encryption and decryption for sensitive credentials
(Bot Tokens, API Hashes, Proxy Secrets), with automatic legacy migration.
"""

import sys
import os
import base64
import ctypes
from typing import Any

DPAPI_PREFIX = "DPAPI:"

# Attempt to import win32crypt, fallback to ctypes Crypt32.dll
_HAS_WIN32CRYPT = False
try:
    import win32crypt
    _HAS_WIN32CRYPT = True
except ImportError:
    pass

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_char))
    ]

def _encrypt_dpapi_ctypes(data_bytes: bytes) -> bytes:
    """Fallback encryption using ctypes CryptProtectData."""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB()
    in_blob.cbData = len(data_bytes)
    in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(data_bytes), ctypes.POINTER(ctypes.c_char))

    out_blob = DATA_BLOB()
    desc = ctypes.c_wchar_p("PC_Remote_Pro_Credential")

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        desc,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError(f"CryptProtectData failed with error {ctypes.GetLastError()}")

    try:
        buffer = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return buffer
    finally:
        kernel32.LocalFree(out_blob.pbData)

def _decrypt_dpapi_ctypes(cipher_bytes: bytes) -> bytes:
    """Fallback decryption using ctypes CryptUnprotectData."""
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_blob = DATA_BLOB()
    in_blob.cbData = len(cipher_bytes)
    in_blob.pbData = ctypes.cast(ctypes.create_string_buffer(cipher_bytes), ctypes.POINTER(ctypes.c_char))

    out_blob = DATA_BLOB()
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob)
    )
    if not ok:
        raise OSError(f"CryptUnprotectData failed with error {ctypes.GetLastError()}")

    try:
        buffer = ctypes.string_at(out_blob.pbData, out_blob.cbData)
        return buffer
    finally:
        kernel32.LocalFree(out_blob.pbData)

def encrypt_credential(plain_text: str) -> str:
    """
    Encrypts a sensitive credential using Windows DPAPI.
    Returns a Base64 string prefixed with 'DPAPI:'.
    If input is empty or already encrypted, returns it unchanged.
    """
    if not plain_text or not isinstance(plain_text, str):
        return ""
    plain_text = plain_text.strip()
    if not plain_text or plain_text.startswith(DPAPI_PREFIX):
        return plain_text

    data_bytes = plain_text.encode("utf-8")
    try:
        if _HAS_WIN32CRYPT:
            cipher_bytes = win32crypt.CryptProtectData(
                data_bytes,
                "PC_Remote_Pro_Credential",
                None,
                None,
                None,
                0
            )
        else:
            cipher_bytes = _encrypt_dpapi_ctypes(data_bytes)
        b64 = base64.b64encode(cipher_bytes).decode("ascii")
        return f"{DPAPI_PREFIX}{b64}"
    except Exception as e:
        # Fallback without failing application boot
        return plain_text

def decrypt_credential(cipher_text: str) -> str:
    """
    Decrypts a credential protected with Windows DPAPI.
    If the string is not prefixed with 'DPAPI:', returns it as plaintext (backward compatibility).
    """
    if not cipher_text or not isinstance(cipher_text, str):
        return ""
    cipher_text = cipher_text.strip()
    if not cipher_text.startswith(DPAPI_PREFIX):
        return cipher_text

    b64_data = cipher_text[len(DPAPI_PREFIX):]
    try:
        cipher_bytes = base64.b64decode(b64_data.encode("ascii"))
        if _HAS_WIN32CRYPT:
            decrypted_bytes = win32crypt.CryptUnprotectData(
                cipher_bytes,
                None,
                None,
                None,
                0
            )[1]
        else:
            decrypted_bytes = _decrypt_dpapi_ctypes(cipher_bytes)
        return decrypted_bytes.decode("utf-8")
    except Exception:
        return ""

def mask_secret(secret: str) -> str:
    """Masks secret tokens and keys for diagnostic logging."""
    if not secret or not isinstance(secret, str):
        return "***MASKED***"
    sec = secret.strip()
    if len(sec) <= 8:
        return "***MASKED***"
    return f"{sec[:4]}...{sec[-4:]} [MASKED]"

def sanitize_account_for_logging(account: dict) -> dict:
    """Returns a copy of an account dict with credentials masked."""
    if not isinstance(account, dict):
        return {}
    clean = dict(account)
    if "token" in clean:
        clean["token"] = mask_secret(decrypt_credential(clean["token"]))
    if "api_hash" in clean:
        clean["api_hash"] = mask_secret(decrypt_credential(clean["api_hash"]))
    if "secret" in clean:
        clean["secret"] = mask_secret(decrypt_credential(clean["secret"]))
    return clean

def migrate_accounts_credentials(accounts: list[dict]) -> tuple[list[dict], bool]:
    """
    Migrates accounts from plaintext to DPAPI-encrypted storage.
    Returns (migrated_accounts, was_migrated_bool).
    """
    migrated = False
    new_accounts = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        item = dict(acc)
        token = str(item.get("token", "")).strip()
        if token and not token.startswith(DPAPI_PREFIX):
            item["token"] = encrypt_credential(token)
            migrated = True

        api_hash = str(item.get("api_hash", "")).strip()
        if api_hash and not api_hash.startswith(DPAPI_PREFIX):
            item["api_hash"] = encrypt_credential(api_hash)
            migrated = True

        new_accounts.append(item)
    return new_accounts, migrated

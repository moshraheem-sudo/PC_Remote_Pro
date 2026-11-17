#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit Tests for telegram_transport Package
Verifies MTProto parser, DPAPI credential protection, secret normalization,
and multi-source aggregation.
"""

import unittest
import os
import base64
from telegram_transport import (
    encrypt_credential,
    decrypt_credential,
    mask_secret,
    migrate_accounts_credentials,
    clean_hostname,
    normalize_mtproto_secret,
    parse_proxy_string,
    MTProxySources
)

class TestMTProxyParser(unittest.TestCase):
    def test_clean_hostname(self):
        self.assertEqual(clean_hostname("https://example.com/"), "example.com")
        self.assertEqual(clean_hostname("1.2.3.4:443"), "1.2.3.4")
        self.assertEqual(clean_hostname("proxy.tg.org."), "proxy.tg.org")

    def test_hex_secret(self):
        raw_hex = "0123456789abcdef0123456789abcdef"
        self.assertEqual(normalize_mtproto_secret(raw_hex), raw_hex)

    def test_fake_tls_secret_preserved(self):
        fake_tls = "eedac1c888d3630f5b1d4ef633d43b9df77777772e617a6164692e676f762e6972"
        res = normalize_mtproto_secret(fake_tls)
        self.assertEqual(res, fake_tls)
        self.assertTrue(res.startswith("ee"))

    def test_base64_secret_conversion(self):
        raw_bytes = bytes.fromhex("0123456789abcdef0123456789abcdef")
        b64 = base64.b64encode(raw_bytes).decode("ascii")
        self.assertEqual(normalize_mtproto_secret(b64), "0123456789abcdef0123456789abcdef")

    def test_urlsafe_base64_secret(self):
        raw_bytes = bytes.fromhex("eeff112233445566778899aabbccddeeff")
        b64_url = base64.urlsafe_b64encode(raw_bytes).decode("ascii").rstrip("=")
        self.assertEqual(normalize_mtproto_secret(b64_url), "eeff112233445566778899aabbccddeeff")

    def test_tg_proxy_url_parsing(self):
        url = "tg://proxy?server=149.154.175.50&port=443&secret=ee139e0ee36150c1ea3bf299796586b5457777772e7674622e7275"
        item = parse_proxy_string(url)
        self.assertIsNotNone(item)
        self.assertEqual(item["server"], "149.154.175.50")
        self.assertEqual(item["port"], 443)
        self.assertTrue(item["is_fake_tls"])
        self.assertTrue(item["secret"].startswith("ee"))

    def test_tme_proxy_url_parsing(self):
        url = "https://t.me/proxy?server=ir-ir.billblog.co.uk&port=8443&secret=79e344ee48705352c8cf510b6dcfec8d"
        item = parse_proxy_string(url)
        self.assertIsNotNone(item)
        self.assertEqual(item["server"], "ir-ir.billblog.co.uk")
        self.assertEqual(item["port"], 8443)
        self.assertFalse(item["is_fake_tls"])

class TestSecureConfig(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self):
        secret = "9013597578:AAFjgc80kaotG_z2fQsQhlVW4AR05rkadyc"
        enc = encrypt_credential(secret)
        self.assertTrue(enc.startswith("DPAPI:"))
        self.assertNotEqual(enc, secret)

        dec = decrypt_credential(enc)
        self.assertEqual(dec, secret)

    def test_legacy_plaintext_passthrough(self):
        plain = "old_unencrypted_token_12345"
        self.assertEqual(decrypt_credential(plain), plain)

    def test_mask_secret(self):
        self.assertEqual(mask_secret("1234567890abcdef"), "1234...cdef [MASKED]")
        self.assertEqual(mask_secret("short"), "***MASKED***")

    def test_migration(self):
        accounts = [
            {"name": "Acc 1", "token": "raw_token_1", "chat_id": 12345},
            {"name": "Acc 2", "token": encrypt_credential("already_enc"), "chat_id": 67890}
        ]
        migrated, was_migrated = migrate_accounts_credentials(accounts)
        self.assertTrue(was_migrated)
        self.assertTrue(migrated[0]["token"].startswith("DPAPI:"))
        self.assertEqual(decrypt_credential(migrated[0]["token"]), "raw_token_1")

if __name__ == "__main__":
    unittest.main()

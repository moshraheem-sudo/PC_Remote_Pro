#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - MTProto Proxy Parser
Parses and normalizes MTProto proxy links, hostnames, ports, and secrets
supporting standard Hex, Base64, URL-Safe Base64, and Fake TLS (ee...) prefixes.
"""

import re
import urllib.parse
import base64
from typing import Optional

PROXY_URI_REGEX = re.compile(
    r'(?:tg://proxy\?[^\s"\'<>]+|https?://t\.me/proxy\?[^\s"\'<>]+)',
    re.IGNORECASE
)

STRUCTURED_LINE_REGEX = re.compile(
    r'([a-zA-Z0-9\.\-_]+)[\s:]+(\d{2,5})[\s:]+([a-zA-Z0-9_\-+=/]+)',
    re.IGNORECASE
)

def clean_hostname(host: str) -> str:
    """Cleans domain or IP host string, stripping trailing dots and protocol prefixes."""
    s = host.strip().rstrip('.')
    if s.startswith("http://") or s.startswith("https://"):
        s = urllib.parse.urlparse(s).netloc
    if ':' in s and not s.startswith('['):
        s = s.split(':')[0]
    return s.strip().rstrip('.').lower()

def normalize_mtproto_secret(raw_secret: str) -> str:
    """
    Normalizes MTProto secret into canonical lowercase hex format expected by TDLib.
    - If already valid hex, preserves exact bytes (including 'ee' Fake TLS and 'dd' prefixes).
    - If Base64 or URL-safe Base64, decodes to raw bytes and converts to hex representation.
    """
    sec = raw_secret.strip()
    if not sec:
        return ""

    # Check if string is already pure hex
    is_hex = True
    try:
        bytes.fromhex(sec)
    except ValueError:
        is_hex = False

    if is_hex:
        return sec.lower()

    # Attempt Base64 / URL-Safe Base64 decoding
    b64_cand = sec.replace('-', '+').replace('_', '/')
    pad = len(b64_cand) % 4
    if pad:
        b64_cand += '=' * (4 - pad)

    try:
        raw_bytes = base64.b64decode(b64_cand)
        # Verify valid decoded length (typically 16 bytes for standard secret or >16 for Fake TLS)
        if len(raw_bytes) >= 16:
            return raw_bytes.hex().lower()
    except Exception:
        pass

    # If decoding fails, return stripped string
    return sec

def parse_proxy_string(text: str) -> Optional[dict]:
    """
    Parses a proxy URL or formatted text into structured proxy dictionary:
    {
        'server': str,
        'port': int,
        'secret': str,
        'link': str,
        'is_fake_tls': bool
    }
    """
    if not text or not isinstance(text, str):
        return None

    line = text.strip()
    server, port_str, secret = "", "", ""

    if "tg://proxy?" in line or "t.me/proxy?" in line:
        url_cand = line if not line.startswith("t.me/") else "https://" + line
        if url_cand.startswith("tg://proxy?"):
            url_cand = url_cand.replace("tg://proxy?", "https://t.me/proxy?")
        try:
            parsed = urllib.parse.urlparse(url_cand)
            qs = urllib.parse.parse_qs(parsed.query)
            server = qs.get("server", [""])[0]
            port_str = qs.get("port", [""])[0]
            secret = qs.get("secret", [""])[0]
        except Exception:
            return None
    else:
        # Match server:port:secret
        parts = line.split(":")
        if len(parts) >= 3:
            server = parts[0]
            port_str = parts[1]
            secret = ":".join(parts[2:])
        else:
            m = STRUCTURED_LINE_REGEX.search(line)
            if m:
                server, port_str, secret = m.group(1), m.group(2), m.group(3)

    server = clean_hostname(server)
    if not server or not port_str or not secret:
        return None

    try:
        port = int(port_str.strip())
        if not (1 <= port <= 65535):
            return None
    except ValueError:
        return None

    norm_secret = normalize_mtproto_secret(secret)
    if not norm_secret:
        return None

    is_fake_tls = norm_secret.startswith("ee")

    return {
        "server": server,
        "port": port,
        "secret": norm_secret,
        "raw_secret": secret.strip(),
        "link": f"tg://proxy?server={server}&port={port}&secret={norm_secret}",
        "is_fake_tls": is_fake_tls
    }

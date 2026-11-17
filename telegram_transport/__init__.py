#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - Telegram MTProto TDLib Transport Package
Provides native TDLib (tdjson.dll) bot connection over MTProto proxies,
Windows DPAPI security, proxy fetching & benchmarking, and command bridge.
"""

from .secure_config import (
    encrypt_credential,
    decrypt_credential,
    mask_secret,
    sanitize_account_for_logging,
    migrate_accounts_credentials,
    DPAPI_PREFIX
)

from .mtproxy_parser import (
    clean_hostname,
    normalize_mtproto_secret,
    parse_proxy_string
)

from .mtproxy_sources import (
    MTProxySources,
    PRIMARY_SOURCE_URL,
    SECONDARY_SOURCE_URL
)

from .mtproxy_manager import (
    MTProxyManager,
    test_proxy_socket
)

from .tdlib_client import TDLibClient
from .tdlib_authorization import TDLibBotAuth
from .telegram_bot_adapter import TelegramBotAdapter

GLOBAL_PROXY_SOURCES = MTProxySources()
GLOBAL_PROXY_MANAGER = MTProxyManager(GLOBAL_PROXY_SOURCES)

__all__ = [
    "encrypt_credential",
    "decrypt_credential",
    "mask_secret",
    "sanitize_account_for_logging",
    "migrate_accounts_credentials",
    "DPAPI_PREFIX",
    "clean_hostname",
    "normalize_mtproto_secret",
    "parse_proxy_string",
    "MTProxySources",
    "PRIMARY_SOURCE_URL",
    "SECONDARY_SOURCE_URL",
    "MTProxyManager",
    "test_proxy_socket",
    "TDLibClient",
    "TDLibBotAuth",
    "TelegramBotAdapter",
    "GLOBAL_PROXY_SOURCES",
    "GLOBAL_PROXY_MANAGER"
]

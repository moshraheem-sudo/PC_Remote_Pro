#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - TDLib Bot Authorization & Proxy Controller
Automates the TDLib authorization handshake for Telegram Bot accounts using
setTdlibParameters, checkAuthenticationBotToken, and MTProto proxy injection.
"""

import os
import threading
from typing import Callable, Optional
from .tdlib_client import TDLibClient

class TDLibBotAuth:
    def __init__(
        self,
        client: TDLibClient,
        api_id: int,
        api_hash: str,
        bot_token: str,
        database_dir: str,
        initial_proxy: Optional[dict] = None,
        app_version: str = "4.2",
        on_ready: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None
    ):
        self.client = client
        self.api_id = int(api_id)
        self.api_hash = str(api_hash).strip()
        self.bot_token = str(bot_token).strip()
        self.database_dir = os.path.abspath(database_dir)
        self.files_dir = os.path.join(self.database_dir, "files")
        self.initial_proxy = initial_proxy
        self.app_version = app_version
        self.on_ready = on_ready
        self.on_error = on_error

        self.current_auth_state: str = "uninitialized"
        self.is_authorized = threading.Event()
        self.auth_error: Optional[str] = None
        self.active_proxy_id: Optional[int] = None
        self._sent_params = False

        # Register authorization update listener
        self.client.add_update_listener("updateAuthorizationState", self._handle_auth_update)

        # Proactively send parameters if client is already waiting
        self._send_tdlib_parameters()

    def _handle_auth_update(self, update: dict):
        state_obj = update.get("authorization_state", {})
        state_type = state_obj.get("@type", "")
        self.current_auth_state = state_type

        if state_type == "authorizationStateWaitTdlibParameters":
            self._send_tdlib_parameters()

        elif state_type == "authorizationStateWaitPhoneNumber":
            # For Bot accounts, we authenticate using checkAuthenticationBotToken
            self._send_bot_token()

        elif state_type == "authorizationStateReady":
            self.is_authorized.set()
            if self.on_ready:
                try:
                    self.on_ready()
                except Exception:
                    pass

        elif state_type == "authorizationStateClosed":
            self.is_authorized.clear()

        elif state_type == "authorizationStateLoggingOut":
            self.is_authorized.clear()

    def _send_tdlib_parameters(self):
        if self._sent_params:
            return
        self._sent_params = True

        os.makedirs(self.database_dir, exist_ok=True)
        os.makedirs(self.files_dir, exist_ok=True)

        params = {
            "@type": "setTdlibParameters",
            "database_directory": self.database_dir,
            "files_directory": self.files_dir,
            "database_encryption_key": "",
            "use_file_database": False,
            "use_chat_info_database": False,
            "use_message_database": True,
            "use_secret_chats": False,
            "api_id": self.api_id,
            "api_hash": self.api_hash,
            "system_language_code": "en",
            "device_model": "PC Remote Pro",
            "system_version": "Windows",
            "application_version": self.app_version,
            "enable_storage_optimizer": True
        }
        resp = self.client.send_request(params, timeout=10.0)
        if resp.get("@type") == "error":
            err = f"Failed to set TDLib parameters: {resp.get('message')}"
            self.auth_error = err
            if self.on_error:
                self.on_error(err)
            return

        # If an initial MTProto proxy was provided, configure it immediately
        if self.initial_proxy:
            self.apply_mtproto_proxy(
                self.initial_proxy["server"],
                self.initial_proxy["port"],
                self.initial_proxy["secret"]
            )

    def _send_bot_token(self):
        req = {
            "@type": "checkAuthenticationBotToken",
            "token": self.bot_token
        }
        resp = self.client.send_request(req, timeout=20.0)
        if resp.get("@type") == "error":
            err = f"Bot token authentication failed: {resp.get('message')}"
            self.auth_error = err
            if self.on_error:
                self.on_error(err)

    def apply_mtproto_proxy(self, server: str, port: int, secret: str) -> bool:
        """
        Adds and enables an MTProto proxy in TDLib.
        """
        proxy_req = {
            "@type": "addProxy",
            "proxy": {
                "@type": "proxy",
                "server": server.strip(),
                "port": int(port),
                "type": {
                    "@type": "proxyTypeMtproto",
                    "secret": secret.strip()
                }
            },
            "enable": True
        }
        res = self.client.send_request(proxy_req, timeout=10.0)
        if res.get("@type") in ("addedProxy", "proxy"):
            self.active_proxy_id = res.get("id")
            return True
        elif res.get("@type") == "ok":
            return True
        return False

    def disable_proxies(self):
        """Disables any active proxy in TDLib (returns to direct connection)."""
        self.client.send_request({"@type": "disableProxy"}, timeout=5.0)
        self.active_proxy_id = None

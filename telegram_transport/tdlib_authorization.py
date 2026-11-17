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

DEFAULT_API_ID = 2040
DEFAULT_API_HASH = "b18441a1ff607e10a989891a5462e627"

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
        try:
            aid = int(api_id)
            if aid in (0, 94575):
                aid = DEFAULT_API_ID
        except Exception:
            aid = DEFAULT_API_ID
        self.api_id = aid

        ahash = str(api_hash or "").strip()
        if not ahash or ahash == "a3406de8d171714214e1e32a2f23e239":
            ahash = DEFAULT_API_HASH
        self.api_hash = ahash

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

        # Register update listeners
        self.client.add_update_listener("updateAuthorizationState", self._handle_auth_update)
        self.client.add_any_update_listener(self._handle_any_update)

    def _handle_any_update(self, update: dict):
        if update.get("@type") == "error":
            msg = update.get("message", "TDLib internal error")
            extra = update.get("@extra", "")
            if "auth" in extra or not self.is_authorized.is_set():
                self.auth_error = msg
                if self.on_error:
                    try:
                        self.on_error(msg)
                    except Exception:
                        pass
        elif update.get("@type") == "addedProxy":
            self.active_proxy_id = update.get("id")

    def _handle_auth_update(self, update: dict):
        state_obj = update.get("authorization_state", {})
        state_type = state_obj.get("@type", "")
        self.current_auth_state = state_type

        if state_type == "authorizationStateWaitTdlibParameters":
            self._send_tdlib_parameters()

        elif state_type == "authorizationStateWaitPhoneNumber":
            # TDLib parameters are processed. Apply proxy first if configured.
            if self.initial_proxy:
                self.apply_mtproto_proxy(
                    self.initial_proxy["server"],
                    self.initial_proxy["port"],
                    self.initial_proxy["secret"]
                )
            # Authenticate bot token
            self._send_bot_token()

        elif state_type == "authorizationStateReady":
            self.is_authorized.set()
            if self.on_ready:
                try:
                    self.on_ready()
                except Exception:
                    pass

        elif state_type in ("authorizationStateClosed", "authorizationStateLoggingOut"):
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
        self.client.send(params)

    def _send_bot_token(self):
        req = {
            "@type": "checkAuthenticationBotToken",
            "@extra": "auth_bot_token",
            "token": self.bot_token
        }
        self.client.send(req)

    def apply_mtproto_proxy(self, server: str, port: int, secret: str):
        """
        Adds and enables an MTProto proxy in TDLib (non-blocking).
        """
        proxy_req = {
            "@type": "addProxy",
            "@extra": "apply_proxy",
            "proxy": {
                "@type": "proxy",
                "server": str(server).strip(),
                "port": int(port),
                "type": {
                    "@type": "proxyTypeMtproto",
                    "secret": str(secret).strip()
                }
            },
            "enable": True
        }
        self.client.send(proxy_req)

    def disable_proxies(self):
        """Disables any active proxy in TDLib (returns to direct connection)."""
        self.client.send({"@type": "disableProxy", "@extra": "disable_proxy"})
        self.active_proxy_id = None

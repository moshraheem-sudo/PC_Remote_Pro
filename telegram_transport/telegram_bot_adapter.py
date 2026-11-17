#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - TDLib Telegram Bot Adapter & Remote Command Dispatcher
Translates TDLib events (updateNewMessage, updateNewCallbackQuery) into PC Remote Pro
commands, enforces chat authorization, formats keyboard markups, and dispatches responses.
"""

import os
import re
import time
import platform
import threading
from typing import Callable, Optional, Any
from .tdlib_client import TDLibClient
from .mtproxy_manager import MTProxyManager

class TelegramBotAdapter:
    def __init__(
        self,
        client: TDLibClient,
        proxy_manager: MTProxyManager,
        authorized_chat_ids: dict[int, dict],
        command_executor: Optional[Callable[[str, int, str, Any], Any]] = None,
        callback_executor: Optional[Callable[[str, int, str, Any], Any]] = None,
        file_receiver: Optional[Callable[[int, str, str], None]] = None
    ):
        self.client = client
        self.proxy_manager = proxy_manager
        self.authorized_users = authorized_chat_ids  # {chat_id: {"name": ..., "acc_id": ...}}
        self.command_executor = command_executor
        self.callback_executor = callback_executor
        self.file_receiver = file_receiver

        self.last_received_msg_time: float = 0.0
        self.last_received_text: str = ""
        self.last_sent_msg_time: float = 0.0
        self.last_sent_text: str = ""
        self.connection_state: str = "connecting"
        self._lock = threading.Lock()

        # Bind event listeners
        self.client.add_update_listener("updateNewMessage", self._on_new_message)
        self.client.add_update_listener("updateNewCallbackQuery", self._on_callback_query)
        self.client.add_update_listener("updateConnectionState", self._on_connection_state)

    def _on_connection_state(self, update: dict):
        state_obj = update.get("state", {})
        raw_state = state_obj.get("@type", "").replace("connectionState", "").lower()
        self.connection_state = raw_state or "connecting"
        if raw_state == "ready":
            self.proxy_manager.mark_connection_success()

    def is_authorized(self, chat_id: int) -> bool:
        """Verifies if the given chat ID is in the registered whitelist."""
        return int(chat_id) in self.authorized_users

    def _on_new_message(self, update: dict):
        message = update.get("message", {})
        if message.get("is_outgoing", False):
            return

        chat_id = message.get("chat_id")
        if not chat_id:
            return

        try:
            chat_id = int(chat_id)
        except ValueError:
            return

        # Security check: ignore or reject unauthorized users
        if not self.is_authorized(chat_id):
            self._send_unauthorized_feedback(chat_id)
            return

        # Extract message content
        content = message.get("content", {})
        content_type = content.get("@type", "")

        msg_text = ""
        if content_type == "messageText":
            msg_text = content.get("text", {}).get("text", "")
        elif content_type in ("messagePhoto", "messageDocument", "messageVideo", "messageAudio", "messageVoiceNote"):
            msg_text = content.get("caption", {}).get("text", "")

        with self._lock:
            self.last_received_msg_time = time.time()
            self.last_received_text = msg_text or f"[{content_type}]"

        # Check for built-in transport monitoring commands
        clean_cmd = msg_text.strip()
        if clean_cmd.startswith("/"):
            parts = clean_cmd.split(None, 1)
            cmd_name = parts[0].lower().split("@")[0]
            cmd_args = parts[1] if len(parts) > 1 else ""

            if cmd_name == "/status":
                self._handle_cmd_status(chat_id)
                return
            elif cmd_name == "/proxy":
                self._handle_cmd_proxy(chat_id)
                return
            elif cmd_name == "/test":
                self._handle_cmd_test(chat_id)
                return
            elif cmd_name == "/reconnect":
                self._handle_cmd_reconnect(chat_id)
                return
            elif cmd_name == "/help":
                self._handle_cmd_help(chat_id)
                return

        # Forward command / message to main PC Remote Pro executor
        if self.command_executor:
            try:
                self.command_executor(msg_text, chat_id, content_type, content)
            except Exception:
                pass

    def _on_callback_query(self, update: dict):
        chat_id = update.get("chat_id")
        query_id = update.get("id")
        data_payload = update.get("payload", {})
        data_bytes = data_payload.get("data", "")

        if not chat_id or not self.is_authorized(int(chat_id)):
            return

        # Decode data payload
        cb_data = ""
        if isinstance(data_bytes, str):
            try:
                import base64
                cb_data = base64.b64decode(data_bytes).decode("utf-8", errors="ignore")
            except Exception:
                cb_data = data_bytes

        # Answer callback query to stop loading spinner
        self.client.send({
            "@type": "answerCallbackQuery",
            "callback_query_id": query_id,
            "text": "Done",
            "show_alert": False
        })

        if self.callback_executor:
            try:
                self.callback_executor(cb_data, int(chat_id), query_id, update)
            except Exception:
                pass

    # --- Built-in Monitoring Commands ---
    def _handle_cmd_status(self, chat_id: int):
        active_px = self.proxy_manager.active_proxy or {}
        srv = active_px.get("server", "None")
        port = active_px.get("port", "None")
        ping = active_px.get("ping", 0.0)
        now_str = time.strftime("%Y-%m-%d %I:%M:%S %p")
        host = platform.node()

        text = (
            f"🟢 **PC Remote Pro - MTProto Status:**\n\n"
            f"💻 **PC Host:** `{host}`\n"
            f"🛡️ **Transport Engine:** `TDLib (v1.8.70 Native)`\n"
            f"🌐 **MTProto Proxy:** `{srv}:{port}`\n"
            f"⚡ **Latency:** `{ping:.0f} ms`\n"
            f"📡 **TDLib State:** `{self.connection_state}`\n"
            f"⏰ **Timestamp:** `{now_str}`\n\n"
            f"Type `/help` for available commands or use the interactive keyboard."
        )
        self.send_message(chat_id, text)

    def _handle_cmd_proxy(self, chat_id: int):
        active_px = self.proxy_manager.active_proxy or {}
        text = (
            f"🛡️ **Active MTProto Proxy Info:**\n\n"
            f"🌐 **Server:** `{active_px.get('server', 'N/A')}`\n"
            f"🔌 **Port:** `{active_px.get('port', 'N/A')}`\n"
            f"⚡ **Ping:** `{active_px.get('ping', 0):.0f} ms`\n"
            f"🔒 **Fake TLS:** `{'Yes (ee...)' if active_px.get('is_fake_tls') else 'Standard'}`\n"
            f"📊 **Ranked Candidates:** `{len(self.proxy_manager.ranked_proxies)} proxies`\n\n"
            f"Use `/reconnect` to switch to next best MTProto proxy."
        )
        self.send_message(chat_id, text)

    def _handle_cmd_test(self, chat_id: int):
        t0 = time.time()
        self.send_message(chat_id, "⚡ Testing MTProto round-trip connection...")
        lat = (time.time() - t0) * 1000.0
        self.send_message(chat_id, f"✅ Connection healthy! Round-trip: `{lat:.0f} ms`")

    def _handle_cmd_reconnect(self, chat_id: int):
        self.send_message(chat_id, "🔄 Switching to next available MTProto proxy...")
        new_px = self.proxy_manager.trigger_failover()
        if new_px:
            # Re-apply proxy in TDLib
            self.client.send({
                "@type": "addProxy",
                "proxy": {
                    "@type": "proxy",
                    "server": new_px["server"],
                    "port": new_px["port"],
                    "type": {
                        "@type": "proxyTypeMtproto",
                        "secret": new_px["secret"]
                    }
                },
                "enable": True
            })
            self.send_message(chat_id, f"✅ Connected to fallback MTProto proxy:\n`{new_px['server']}:{new_px['port']}`")
        else:
            self.send_message(chat_id, "⚠️ No alternate working MTProto proxy found.")

    def _handle_cmd_help(self, chat_id: int):
        help_text = (
            "📖 **PC Remote Pro - Remote Commands:**\n\n"
            "• `/status` - Check PC & TDLib MTProto status\n"
            "• `/proxy` - View active MTProto proxy details\n"
            "• `/test` - Test round-trip latency\n"
            "• `/reconnect` - Failover to next MTProto proxy\n"
            "• `/screen` - Capture PC screenshot\n"
            "• `/cam` - Capture webcam snapshot\n"
            "• `/mic [sec]` - Record microphone audio\n"
            "• `/specs` - Detailed PC specs & temperatures\n"
            "• `/wifi` - Wi-Fi network & IP information\n"
            "• `/cmd [command]` - Execute CMD shell command\n"
            "• `/py [code]` - Run Python code remotely\n"
            "• `/msg [text]` - Show pop-up message on PC screen\n"
            "• `/say [text]` - Speak text using PC speakers (TTS)\n"
            "• `/apps` - List running processes\n"
            "• `/kill [name]` - Terminate process\n"
            "• `/yt [url]` - Play YouTube video on PC\n"
            "• `/guard` - Security guard motion/intruder mode"
        )
        self.send_message(chat_id, help_text)

    def _send_unauthorized_feedback(self, chat_id: int):
        host = platform.node()
        text = (
            f"⚠️ **Security Alert - PC ({host}):**\n\n"
            f"Your account is not authorized to control this computer.\n"
            f"🆔 **Your Chat ID:** `{chat_id}`\n\n"
            f"📌 To authorize: add this Chat ID in PC Remote Pro on the computer."
        )
        self.send_message(chat_id, text)

    # --- Outgoing Dispatch Methods ---
    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None) -> bool:
        """Sends a text message through TDLib with formatted text and optional keyboard markup."""
        input_content = {
            "@type": "inputMessageText",
            "text": {
                "@type": "formattedText",
                "text": text
            }
        }
        req = {
            "@type": "sendMessage",
            "chat_id": int(chat_id),
            "input_message_content": input_content
        }
        if reply_markup:
            req["reply_markup"] = reply_markup

        resp = self.client.send_request(req, timeout=12.0)
        with self._lock:
            self.last_sent_msg_time = time.time()
            self.last_sent_text = text[:80]
        return resp.get("@type") == "message"

    def send_document(self, chat_id: int, file_path: str, caption: str = "") -> bool:
        """Uploads and delivers a file/document to the user via TDLib."""
        if not os.path.exists(file_path):
            return False

        input_content = {
            "@type": "inputMessageDocument",
            "document": {
                "@type": "inputFileLocal",
                "path": os.path.abspath(file_path)
            },
            "caption": {
                "@type": "formattedText",
                "text": caption or os.path.basename(file_path)
            }
        }
        req = {
            "@type": "sendMessage",
            "chat_id": int(chat_id),
            "input_message_content": input_content
        }
        resp = self.client.send_request(req, timeout=60.0)
        with self._lock:
            self.last_sent_msg_time = time.time()
            self.last_sent_text = f"[Document]: {os.path.basename(file_path)}"
        return resp.get("@type") == "message"

    def send_photo(self, chat_id: int, photo_path: str, caption: str = "") -> bool:
        """Uploads and delivers a photo to the user via TDLib."""
        if not os.path.exists(photo_path):
            return False

        input_content = {
            "@type": "inputMessagePhoto",
            "photo": {
                "@type": "inputFileLocal",
                "path": os.path.abspath(photo_path)
            },
            "caption": {
                "@type": "formattedText",
                "text": caption
            }
        }
        req = {
            "@type": "sendMessage",
            "chat_id": int(chat_id),
            "input_message_content": input_content
        }
        resp = self.client.send_request(req, timeout=30.0)
        with self._lock:
            self.last_sent_msg_time = time.time()
            self.last_sent_text = f"[Photo]: {os.path.basename(photo_path)}"
        return resp.get("@type") == "message"

    def send_voice(self, chat_id: int, voice_path: str, caption: str = "") -> bool:
        """Uploads and delivers a voice note to the user via TDLib."""
        if not os.path.exists(voice_path):
            return False

        input_content = {
            "@type": "inputMessageVoiceNote",
            "voice_note": {
                "@type": "inputFileLocal",
                "path": os.path.abspath(voice_path)
            },
            "caption": {
                "@type": "formattedText",
                "text": caption
            }
        }
        req = {
            "@type": "sendMessage",
            "chat_id": int(chat_id),
            "input_message_content": input_content
        }
        resp = self.client.send_request(req, timeout=30.0)
        return resp.get("@type") == "message"

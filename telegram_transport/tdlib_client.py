#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - TDLib C JSON API Client & Event Dispatcher
Provides thread-safe ctypes integration with official Telegram TDLib (tdjson.dll),
dedicated non-blocking receiver thread, request-reply correlation, and update handlers.
"""

import sys
import os
import json
import time
import ctypes
import threading
import queue
from typing import Callable, Optional, Any

class TDLibClient:
    def __init__(self, dll_path: Optional[str] = None):
        self.client_id: Optional[int] = None
        self._tdjson = self._load_tdjson_library(dll_path)
        self._is_running = False
        self._receiver_thread: Optional[threading.Thread] = None
        self._pending_requests: dict[str, dict] = {}
        self._requests_lock = threading.Lock()
        self._update_listeners: dict[str, list[Callable[[dict], None]]] = {}
        self._any_update_listeners: list[Callable[[dict], None]] = []
        self._request_counter = 0

        # Initialize client instance
        self.client_id = self._tdjson.td_create_client_id()
        self._is_running = True

        # Silence verbose TDLib logs
        self.execute({"@type": "setLogVerbosityLevel", "new_verbosity_level": 1})

        # Start dedicated background receive loop
        self._receiver_thread = threading.Thread(target=self._receive_loop, daemon=True, name="TDLib-Receiver")
        self._receiver_thread.start()

    @staticmethod
    def _find_tdjson_dll(custom_path: Optional[str] = None) -> str:
        """Finds tdjson.dll across bundle and filesystem search locations."""
        if custom_path and os.path.exists(custom_path):
            return os.path.abspath(custom_path)

        candidates = []
        # 1. PyInstaller unpacked runtime directory
        if hasattr(sys, "_MEIPASS"):
            candidates.append(os.path.join(sys._MEIPASS, "tdjson.dll"))
            candidates.append(os.path.join(sys._MEIPASS, "tdlib_bin", "tdjson.dll"))

        # 2. Main application directory
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(base_dir, "tdjson.dll"))
        candidates.append(os.path.join(base_dir, "tdlib_bin", "tdjson.dll"))
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "tdjson.dll"))

        # 3. Current working directory
        candidates.append(os.path.abspath("tdjson.dll"))
        candidates.append(os.path.abspath(os.path.join("tdlib_bin", "tdjson.dll")))

        for path in candidates:
            if os.path.exists(path):
                return os.path.abspath(path)

        raise FileNotFoundError(
            f"Could not locate tdjson.dll for Windows x64. Checked paths: {candidates}"
        )

    def _load_tdjson_library(self, dll_path: Optional[str]) -> ctypes.CDLL:
        resolved_path = self._find_tdjson_dll(dll_path)
        dll_dir = os.path.dirname(resolved_path)

        # Add dll directory to search path for libcrypto, libssl, and zlib
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(dll_dir)
            except Exception:
                pass

        lib = ctypes.CDLL(resolved_path)

        # Configure ctypes signatures for TDLib JSON C API
        lib.td_create_client_id.restype = ctypes.c_int
        lib.td_create_client_id.argtypes = []

        lib.td_send.restype = None
        lib.td_send.argtypes = [ctypes.c_int, ctypes.c_char_p]

        lib.td_receive.restype = ctypes.c_char_p
        lib.td_receive.argtypes = [ctypes.c_double]

        lib.td_execute.restype = ctypes.c_char_p
        lib.td_execute.argtypes = [ctypes.c_char_p]

        return lib

    def execute(self, query: dict) -> Optional[dict]:
        """Synchronously executes a local TDLib method that does not require network (e.g. setLogVerbosityLevel)."""
        raw = json.dumps(query).encode("utf-8")
        res = self._tdjson.td_execute(raw)
        if res:
            try:
                return json.loads(res.decode("utf-8"))
            except Exception:
                return None
        return None

    def send(self, query: dict):
        """Asynchronously sends a request to TDLib without waiting for response."""
        if not self._is_running or self.client_id is None:
            return
        raw = json.dumps(query).encode("utf-8")
        self._tdjson.td_send(self.client_id, raw)

    def send_request(self, query: dict, timeout: float = 15.0) -> dict:
        """
        Sends an asynchronous request and blocks the calling thread until
        the response with matching @extra is received or timeout occurs.
        """
        if not self._is_running or self.client_id is None:
            return {"@type": "error", "code": 500, "message": "TDLib client is stopped"}

        req = dict(query)
        with self._requests_lock:
            self._request_counter += 1
            extra_id = f"req_{self._request_counter}_{time.time()}"
            req["@extra"] = extra_id
            resp_event = threading.Event()
            entry = {"event": resp_event, "response": None}
            self._pending_requests[extra_id] = entry

        self.send(req)

        # Wait for correlated response
        finished = resp_event.wait(timeout)
        with self._requests_lock:
            self._pending_requests.pop(extra_id, None)

        if not finished or entry["response"] is None:
            return {"@type": "error", "code": 408, "message": "TDLib request timed out"}

        return entry["response"]

    def add_update_listener(self, update_type: str, callback: Callable[[dict], None]):
        """Registers a listener for a specific TDLib update type (e.g., 'updateNewMessage')."""
        self._update_listeners.setdefault(update_type, []).append(callback)

    def add_any_update_listener(self, callback: Callable[[dict], None]):
        """Registers a listener for all TDLib updates."""
        self._any_update_listeners.append(callback)

    def _receive_loop(self):
        """Dedicated single-consumer thread for td_receive."""
        while self._is_running:
            try:
                raw = self._tdjson.td_receive(1.0)
                if not raw:
                    continue
                data = json.loads(raw.decode("utf-8", errors="ignore"))
            except Exception:
                continue

            extra_id = data.get("@extra")
            if extra_id:
                with self._requests_lock:
                    pending = self._pending_requests.get(extra_id)
                    if pending:
                        pending["response"] = data
                        pending["event"].set()

            update_type = data.get("@type", "")
            # Dispatch specific listeners
            if update_type in self._update_listeners:
                for cb in self._update_listeners[update_type]:
                    try:
                        cb(data)
                    except Exception:
                        pass

            # Dispatch generic listeners
            for cb in self._any_update_listeners:
                try:
                    cb(data)
                except Exception:
                    pass

            if update_type == "updateAuthorizationState":
                auth_state = data.get("authorization_state", {}).get("@type", "")
                if auth_state == "authorizationStateClosed":
                    break

    def close(self):
        """Gracefully closes TDLib client and waits for receiver thread."""
        if not self._is_running:
            return
        self._is_running = False
        try:
            self.send({"@type": "close"})
        except Exception:
            pass
        if self._receiver_thread and self._receiver_thread.is_alive():
            self._receiver_thread.join(timeout=3.0)

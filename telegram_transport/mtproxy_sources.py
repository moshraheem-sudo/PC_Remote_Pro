#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - MTProto Proxy Sources & Automatic Feed Aggregator
Fetches, deduplicates, and caches fresh MTProto proxies from SoliSpirit and
mtproto-auto-proxies repositories with resilient failover and local persistence.
"""

import os
import json
import time
import threading
import urllib.request
from typing import Callable, Optional
from .mtproxy_parser import parse_proxy_string

PRIMARY_SOURCE_URL = "https://raw.githubusercontent.com/SoliSpirit/mtproto/master/all_proxies.txt"
SECONDARY_SOURCE_URL = "https://raw.githubusercontent.com/moshraheem-sudo/mtproto-auto-proxies/main/proxies.txt"

DEFAULT_SOURCES = [
    ("SoliSpirit/mtproto", PRIMARY_SOURCE_URL),
    ("mtproto-auto-proxies", SECONDARY_SOURCE_URL)
]

class MTProxySources:
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self.cache_file = os.path.join(cache_dir, "mtproxy_cache.json")
        self.proxies: list[dict] = []
        self.last_fetch_time: float = 0.0
        self.is_fetching: bool = False
        self._lock = threading.Lock()
        self._bg_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.listeners: list[Callable[[list[dict]], None]] = []

        # Load cached proxies on instantiation
        self._load_cache()

    def add_listener(self, callback: Callable[[list[dict]], None]):
        """Registers a listener to be notified when proxy list is updated."""
        if callback not in self.listeners:
            self.listeners.append(callback)

    def _load_cache(self):
        """Loads cached proxies from local JSON file."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.proxies = data.get("proxies", [])
                        self.last_fetch_time = data.get("timestamp", 0.0)
            except Exception:
                pass

    def _save_cache(self):
        """Saves current proxies to local cache file atomically."""
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp_file = self.cache_file + ".tmp"
            payload = {
                "timestamp": self.last_fetch_time,
                "count": len(self.proxies),
                "proxies": self.proxies
            }
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            if os.path.exists(self.cache_file):
                os.remove(self.cache_file)
            os.rename(tmp_file, self.cache_file)
        except Exception:
            pass

    def fetch_all_sources(self) -> list[dict]:
        """
        Synchronously fetches both remote feeds, parses, deduplicates,
        and returns the consolidated proxy list.
        """
        with self._lock:
            if self.is_fetching:
                return list(self.proxies)
            self.is_fetching = True

        try:
            all_raw_lines = []
            for name, url in DEFAULT_SOURCES:
                try:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PCRemotePro/4.2"}
                    )
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        content = resp.read().decode("utf-8", errors="ignore")
                    lines = [l.strip() for l in content.splitlines() if l.strip() and not l.startswith("#")]
                    all_raw_lines.extend(lines)
                except Exception:
                    # Keep going to next source
                    continue

            new_proxies = []
            seen = set()

            for line in all_raw_lines:
                item = parse_proxy_string(line)
                if item:
                    key = f"{item['server']}:{item['port']}:{item['secret']}"
                    if key not in seen:
                        seen.add(key)
                        new_proxies.append(item)

            with self._lock:
                if new_proxies:
                    self.proxies = new_proxies
                    self.last_fetch_time = time.time()
                    self._save_cache()
                elif not self.proxies:
                    # In case both failed and no cache, keep previous
                    pass

                result = list(self.proxies)

            # Notify listeners
            for cb in self.listeners:
                try:
                    cb(result)
                except Exception:
                    pass

            return result
        finally:
            with self._lock:
                self.is_fetching = False

    def fetch_async(self, callback: Optional[Callable[[list[dict]], None]] = None):
        """Dispatches proxy fetching in background daemon thread."""
        def _worker():
            res = self.fetch_all_sources()
            if callback:
                try:
                    callback(res)
                except Exception:
                    pass

        threading.Thread(target=_worker, daemon=True).start()

    def start_periodic_updater(self, interval_seconds: int = 7200):
        """Starts background loop updating proxy list every N seconds (default: 2 hours)."""
        if self._bg_thread and self._bg_thread.is_alive():
            return

        self._stop_event.clear()

        def _loop():
            # Initial fetch on startup if cache is empty or older than 1 hour
            if not self.proxies or (time.time() - self.last_fetch_time > 3600):
                self.fetch_all_sources()

            while not self._stop_event.is_set():
                self._stop_event.wait(interval_seconds)
                if self._stop_event.is_set():
                    break
                self.fetch_all_sources()

        self._bg_thread = threading.Thread(target=_loop, daemon=True)
        self._bg_thread.start()

    def stop_periodic_updater(self):
        """Stops the background updater loop."""
        self._stop_event.set()

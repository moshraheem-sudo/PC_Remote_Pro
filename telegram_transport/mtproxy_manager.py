#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PC Remote Pro - MTProto Proxy Manager & Intelligent Failover Controller
Tests, benchmarks, selects, and switches MTProto proxies with exponential backoff,
health tracking, and real-time event reporting.
"""

import time
import socket
import threading
from typing import Callable, Optional
from concurrent.futures import ThreadPoolExecutor
from .mtproxy_sources import MTProxySources

def test_proxy_socket(server: str, port: int, timeout: float = 2.5) -> tuple[bool, float]:
    """Measures TCP handshake latency (ms) to the MTProto server."""
    t0 = time.time()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((server, port))
        lat = (time.time() - t0) * 1000.0
        s.close()
        return True, round(lat, 1)
    except Exception:
        return False, 9999.0

class MTProxyManager:
    def __init__(self, sources: MTProxySources):
        self.sources = sources
        self.active_proxy: Optional[dict] = None
        self.ranked_proxies: list[dict] = []
        self._current_index: int = 0
        self._lock = threading.Lock()
        self.consecutive_fails: int = 0
        self.last_switch_time: float = 0.0
        self.last_successful_connection: float = 0.0
        self.on_proxy_changed: list[Callable[[dict], None]] = []
        self.on_status_changed: list[Callable[[str, str], None]] = []

    def add_change_listener(self, cb: Callable[[dict], None]):
        if cb not in self.on_proxy_changed:
            self.on_proxy_changed.append(cb)

    def add_status_listener(self, cb: Callable[[str, str], None]):
        if cb not in self.on_status_changed:
            self.on_status_changed.append(cb)

    def _notify_change(self, proxy: dict):
        for cb in self.on_proxy_changed:
            try:
                cb(proxy)
            except Exception:
                pass

    def _notify_status(self, state: str, detail: str):
        for cb in self.on_status_changed:
            try:
                cb(state, detail)
            except Exception:
                pass

    def benchmark_and_rank_proxies(self, max_test: int = 40) -> list[dict]:
        """
        Tests top candidate proxies concurrently and sorts them by lowest latency.
        """
        raw_list = self.sources.proxies
        if not raw_list:
            raw_list = self.sources.fetch_all_sources()

        candidates = raw_list[:max_test]
        working = []

        def _probe(item):
            ok, lat = test_proxy_socket(item["server"], item["port"])
            if ok:
                entry = dict(item)
                entry["ping"] = lat
                entry["tested_at"] = time.time()
                return entry
            return None

        with ThreadPoolExecutor(max_workers=20) as pool:
            for res in pool.map(_probe, candidates):
                if res:
                    working.append(res)

        working.sort(key=lambda x: x["ping"])
        with self._lock:
            self.ranked_proxies = working
            self._current_index = 0
            if working:
                self.active_proxy = working[0]
            else:
                self.active_proxy = None

        if self.active_proxy:
            self._notify_change(self.active_proxy)
            self._notify_status("ready", f"Selected {self.active_proxy['server']}:{self.active_proxy['port']} ({self.active_proxy.get('ping')}ms)")
        else:
            self._notify_status("failed", "No reachable MTProto proxies found")

        return working

    def get_current_or_best(self) -> Optional[dict]:
        """Returns the active proxy or benchmarks candidate list."""
        with self._lock:
            if self.active_proxy:
                return dict(self.active_proxy)

        ranked = self.benchmark_and_rank_proxies(max_test=25)
        if ranked:
            return ranked[0]
        return None

    def trigger_failover(self) -> Optional[dict]:
        """
        Switches to the next working proxy in the ranked list with backoff.
        """
        with self._lock:
            self.consecutive_fails += 1
            now = time.time()
            if self.ranked_proxies and len(self.ranked_proxies) > 1:
                self._current_index = (self._current_index + 1) % len(self.ranked_proxies)
                self.active_proxy = self.ranked_proxies[self._current_index]
                self.last_switch_time = now
                selected = dict(self.active_proxy)
            else:
                selected = None

        if selected:
            self._notify_change(selected)
            self._notify_status("failover", f"Switched to fallback proxy: {selected['server']}:{selected['port']}")
            return selected

        # If ranked list is exhausted or empty, re-rank
        ranked = self.benchmark_and_rank_proxies(max_test=35)
        if ranked:
            return ranked[0]
        return None

    def mark_connection_success(self):
        """Called when TDLib successfully completes authentication or message delivery."""
        with self._lock:
            self.consecutive_fails = 0
            self.last_successful_connection = time.time()
        self._notify_status("connected", "MTProto connection verified active")

"""kabuステーションAPIのPUSH配信(WebSocket)を購読し、分足バーへ集計するサービス。

PUSH配信には約定通知は含まれず、気配・現在値のみが流れてくる点に注意
(注文約定の検知は order_manager が /orders をポーリングして行う)。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import pandas as pd
import websocket

from autotrade.bar_builder import BarBuilder
from autotrade.models import Bar, SymbolSpec

logger = logging.getLogger(__name__)


def _parse_price_time(raw: Optional[str]) -> datetime:
    if raw:
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
    return datetime.now(timezone.utc).astimezone()


class MarketDataService:
    def __init__(
        self,
        ws_url: str,
        get_token: Callable[[], str],
        symbols: list[SymbolSpec],
        bar_interval_sec: int,
        max_bars: int = 500,
        reconnect_wait_sec: float = 3.0,
    ) -> None:
        self._ws_url = ws_url
        self._get_token = get_token
        self._symbols = symbols
        self._bar_interval_sec = bar_interval_sec
        self._max_bars = max_bars
        self._reconnect_wait_sec = reconnect_wait_sec

        self._lock = threading.Lock()
        self._builders: dict[str, BarBuilder] = {
            s.symbol: BarBuilder(s.symbol, bar_interval_sec, on_bar_closed=self._on_bar_closed)
            for s in symbols
        }
        self._history: dict[str, list[Bar]] = {s.symbol: [] for s in symbols}
        self._latest_price: dict[str, float] = {}
        self._bar_listeners: list[Callable[[Bar], None]] = []

        self._ws: Optional[websocket.WebSocketApp] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    def add_bar_listener(self, callback: Callable[[Bar], None]) -> None:
        self._bar_listeners.append(callback)

    def _on_bar_closed(self, bar: Bar) -> None:
        with self._lock:
            hist = self._history[bar.symbol]
            hist.append(bar)
            if len(hist) > self._max_bars:
                del hist[: len(hist) - self._max_bars]
        for cb in self._bar_listeners:
            try:
                cb(bar)
            except Exception:  # noqa: BLE001 - リスナー例外でループを止めない
                logger.exception("bar listener でエラーが発生しました。")

    def get_bars_df(self, symbol: str) -> pd.DataFrame:
        with self._lock:
            bars = list(self._history.get(symbol, []))
        if not bars:
            return pd.DataFrame(columns=["start", "open", "high", "low", "close"]).set_index("start")
        df = pd.DataFrame(
            {
                "start": [b.start for b in bars],
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
            }
        )
        return df.set_index("start")

    def latest_price(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._latest_price.get(symbol)

    def flush_all_bars(self) -> None:
        """未確定バーを全銘柄分確定させる(強制フラット直前などに利用)。"""
        for builder in self._builders.values():
            builder.flush()

    # ------------------------------------------------------------------
    # WebSocket 接続管理
    # ------------------------------------------------------------------
    def _handle_message(self, raw_message: str) -> None:
        try:
            data = json.loads(raw_message)
        except json.JSONDecodeError:
            logger.warning("PUSH配信のJSONパースに失敗しました: %r", raw_message[:200])
            return

        symbol = data.get("Symbol")
        price = data.get("CurrentPrice")
        if symbol is None or price in (None, 0):
            return
        if symbol not in self._builders:
            return

        ts = _parse_price_time(data.get("CurrentPriceTime"))
        with self._lock:
            self._latest_price[symbol] = float(price)
        self._builders[symbol].on_tick(float(price), ts)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_forever, name="kabu-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._ws is not None:
            self._ws.close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            try:
                token = self._get_token()
                self._ws = websocket.WebSocketApp(
                    self._ws_url,
                    header=[f"X-API-KEY: {token}"],
                    on_message=lambda ws, msg: self._handle_message(msg),
                    on_error=lambda ws, err: logger.warning("WebSocketエラー: %s", err),
                    on_close=lambda ws, code, msg: logger.info("WebSocket切断 (code=%s)", code),
                    on_open=lambda ws: logger.info("WebSocket接続完了: %s", self._ws_url),
                )
                self._ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception:  # noqa: BLE001
                logger.exception("WebSocket接続でエラーが発生しました。再接続します。")
            if self._stop_event.is_set():
                break
            time.sleep(self._reconnect_wait_sec)

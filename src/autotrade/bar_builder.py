"""ティック(現在値)から分足バーを組み立てる純粋ロジック。

kabuステーションAPIはヒストリカルOHLCを提供しないため、PUSH配信される
現在値(CurrentPrice)を自前で `bar_interval_sec` 単位に集計してバー化する。
I/Oを持たないため単体テストしやすい。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional

from autotrade.models import Bar


def _bucket_start(ts: datetime, interval_sec: int) -> datetime:
    epoch_sec = int(ts.timestamp())
    bucket_epoch = epoch_sec - (epoch_sec % interval_sec)
    return datetime.fromtimestamp(bucket_epoch, tz=ts.tzinfo)


class BarBuilder:
    """1銘柄分のティック→バー集計器。"""

    def __init__(
        self,
        symbol: str,
        interval_sec: int,
        on_bar_closed: Optional[Callable[[Bar], None]] = None,
    ) -> None:
        self.symbol = symbol
        self.interval_sec = interval_sec
        self._on_bar_closed = on_bar_closed
        self._current: Optional[Bar] = None

    def on_tick(self, price: float, ts: datetime) -> Optional[Bar]:
        """ティックを1つ取り込む。バーが確定した場合はそのBarを返す(コールバックも呼ぶ)。"""
        bucket = _bucket_start(ts, self.interval_sec)
        closed: Optional[Bar] = None

        if self._current is None:
            self._current = Bar(
                symbol=self.symbol, start=bucket, open=price, high=price, low=price, close=price, tick_count=1
            )
            return None

        if bucket > self._current.start:
            closed = self._current
            self._current = Bar(
                symbol=self.symbol, start=bucket, open=price, high=price, low=price, close=price, tick_count=1
            )
        elif bucket < self._current.start:
            # 遅延/順序逆転して届いたティック。現在バーより古いので無視する。
            return None
        else:
            c = self._current
            c.high = max(c.high, price)
            c.low = min(c.low, price)
            c.close = price
            c.tick_count += 1

        if closed is not None and self._on_bar_closed is not None:
            self._on_bar_closed(closed)
        return closed

    def flush(self) -> Optional[Bar]:
        """未確定の現在バーを強制的に確定させる(セッション終了時などに使用)。"""
        if self._current is None:
            return None
        closed = self._current
        self._current = None
        if self._on_bar_closed is not None:
            self._on_bar_closed(closed)
        return closed

    @property
    def current_bar(self) -> Optional[Bar]:
        return self._current

"""移動平均クロス戦略(SMAゴールデンクロス/デッドクロス)。

- ノーポジション時: ゴールデンクロスで新規買い(ENTRY_LONG)、
  デッドクロスで新規売り(ENTRY_SHORT、いわゆる「空売り」の信用新規)を出す。
- 建玉保有中: 保有方向と逆のクロスが出たら EXIT(手仕舞い)を出す。
  (損切り/利確ラインの判定は risk.py 側、こちらはトレンド転換シグナルのみを扱う)
"""
from __future__ import annotations

import pandas as pd

from autotrade.indicators import dead_cross, golden_cross, sma
from autotrade.models import Signal
from autotrade.strategy.base import Strategy


class MovingAverageCrossStrategy(Strategy):
    name = "ma_cross"

    def __init__(self, fast_period: int = 5, slow_period: int = 25) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period は slow_period より小さくしてください。")
        self.fast_period = fast_period
        self.slow_period = slow_period

    @property
    def warmup_bars(self) -> int:
        return self.slow_period + 1

    def evaluate(
        self,
        bars: pd.DataFrame,
        *,
        has_open_position: bool,
        position_side: str | None,
    ) -> Signal:
        if len(bars) < self.warmup_bars:
            return Signal.NONE

        closes = bars["close"]
        fast = sma(closes, self.fast_period)
        slow = sma(closes, self.slow_period)

        is_golden = golden_cross(fast, slow)
        is_dead = dead_cross(fast, slow)

        if has_open_position:
            if position_side == "2" and is_dead:  # 買建中にデッドクロス→手仕舞い
                return Signal.EXIT
            if position_side == "1" and is_golden:  # 売建中にゴールデンクロス→手仕舞い
                return Signal.EXIT
            return Signal.NONE

        if is_golden:
            return Signal.ENTRY_LONG
        if is_dead:
            return Signal.ENTRY_SHORT
        return Signal.NONE

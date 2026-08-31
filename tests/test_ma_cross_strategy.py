import pandas as pd
import pytest

from autotrade.models import Signal
from autotrade.strategy.ma_cross import MovingAverageCrossStrategy


def make_bars(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


def test_rejects_invalid_periods():
    with pytest.raises(ValueError):
        MovingAverageCrossStrategy(fast_period=10, slow_period=5)


def test_no_signal_before_warmup():
    strat = MovingAverageCrossStrategy(fast_period=2, slow_period=4)
    bars = make_bars([100, 101, 102])  # < warmup_bars(5)
    signal = strat.evaluate(bars, has_open_position=False, position_side=None)
    assert signal is Signal.NONE


def test_entry_long_on_golden_cross():
    strat = MovingAverageCrossStrategy(fast_period=2, slow_period=4)
    # slow(4期間)平均が緩やかに上昇 vs fast(2期間)平均が急騰し下から上抜け
    closes = [100, 100, 100, 100, 100, 130]
    bars = make_bars(closes)
    signal = strat.evaluate(bars, has_open_position=False, position_side=None)
    assert signal is Signal.ENTRY_LONG


def test_entry_short_on_dead_cross():
    strat = MovingAverageCrossStrategy(fast_period=2, slow_period=4)
    closes = [100, 100, 100, 100, 100, 70]
    bars = make_bars(closes)
    signal = strat.evaluate(bars, has_open_position=False, position_side=None)
    assert signal is Signal.ENTRY_SHORT


def test_exit_long_on_dead_cross_while_holding():
    strat = MovingAverageCrossStrategy(fast_period=2, slow_period=4)
    closes = [100, 100, 100, 100, 100, 70]
    bars = make_bars(closes)
    signal = strat.evaluate(bars, has_open_position=True, position_side="2")
    assert signal is Signal.EXIT


def test_no_new_entry_signal_while_already_holding_same_direction():
    strat = MovingAverageCrossStrategy(fast_period=2, slow_period=4)
    closes = [100, 100, 100, 100, 100, 130]  # ゴールデンクロス
    bars = make_bars(closes)
    # 既に買建(ロング)を保有中はゴールデンクロスが起きてもEXIT判定にはならず、NONE
    signal = strat.evaluate(bars, has_open_position=True, position_side="2")
    assert signal is Signal.NONE

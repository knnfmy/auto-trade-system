from datetime import datetime, timedelta, timezone

from autotrade.bar_builder import BarBuilder

JST = timezone(timedelta(hours=9))


def dt(minute: int, second: int = 0) -> datetime:
    return datetime(2024, 1, 4, 9, minute, second, tzinfo=JST)


def test_ticks_within_same_interval_form_one_bar():
    closed_bars = []
    builder = BarBuilder("7203", interval_sec=60, on_bar_closed=closed_bars.append)

    builder.on_tick(100.0, dt(0, 0))
    builder.on_tick(105.0, dt(0, 10))
    builder.on_tick(95.0, dt(0, 20))
    builder.on_tick(102.0, dt(0, 59))

    assert closed_bars == []  # まだバーは確定していない
    current = builder.current_bar
    assert current.open == 100.0
    assert current.high == 105.0
    assert current.low == 95.0
    assert current.close == 102.0
    assert current.tick_count == 4


def test_new_interval_closes_previous_bar():
    closed_bars = []
    builder = BarBuilder("7203", interval_sec=60, on_bar_closed=closed_bars.append)

    builder.on_tick(100.0, dt(0, 0))
    builder.on_tick(102.0, dt(0, 30))
    result = builder.on_tick(110.0, dt(1, 5))  # 次の1分バーに突入

    assert result is not None
    assert result.close == 102.0
    assert len(closed_bars) == 1
    assert closed_bars[0] is result
    assert builder.current_bar.open == 110.0


def test_out_of_order_tick_is_ignored():
    builder = BarBuilder("7203", interval_sec=60)
    builder.on_tick(100.0, dt(1, 0))
    builder.on_tick(999.0, dt(0, 30))  # 過去のバケットなので無視される
    assert builder.current_bar.close == 100.0
    assert builder.current_bar.high == 100.0


def test_flush_closes_current_bar():
    closed_bars = []
    builder = BarBuilder("7203", interval_sec=60, on_bar_closed=closed_bars.append)
    builder.on_tick(100.0, dt(0, 0))
    flushed = builder.flush()

    assert flushed.close == 100.0
    assert closed_bars == [flushed]
    assert builder.current_bar is None

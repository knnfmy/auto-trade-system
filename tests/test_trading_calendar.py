from datetime import datetime, time
from zoneinfo import ZoneInfo

from autotrade.trading_calendar import TradingCalendar, is_trading_day

JST = ZoneInfo("Asia/Tokyo")


def test_weekend_is_not_trading_day():
    assert is_trading_day(datetime(2024, 1, 6, tzinfo=JST).date()) is False  # 土曜
    assert is_trading_day(datetime(2024, 1, 7, tzinfo=JST).date()) is False  # 日曜


def test_year_end_new_year_is_not_trading_day():
    assert is_trading_day(datetime(2024, 12, 31, tzinfo=JST).date()) is False
    assert is_trading_day(datetime(2024, 1, 2, tzinfo=JST).date()) is False


def test_ordinary_weekday_is_trading_day():
    assert is_trading_day(datetime(2024, 1, 4, tzinfo=JST).date()) is True  # 木曜


def _calendar() -> TradingCalendar:
    return TradingCalendar(
        session_start=time(9, 0),
        session_end=time(15, 0),
        force_flatten_time=time(14, 55),
    )


def test_is_market_open_within_session():
    cal = _calendar()
    when = datetime(2024, 1, 4, 10, 0, tzinfo=JST)
    assert cal.is_market_open(when) is True


def test_is_market_open_before_session():
    cal = _calendar()
    when = datetime(2024, 1, 4, 8, 59, tzinfo=JST)
    assert cal.is_market_open(when) is False


def test_should_force_flatten_window():
    cal = _calendar()
    before = datetime(2024, 1, 4, 14, 54, tzinfo=JST)
    during = datetime(2024, 1, 4, 14, 56, tzinfo=JST)
    after_close = datetime(2024, 1, 4, 15, 1, tzinfo=JST)

    assert cal.should_force_flatten(before) is False
    assert cal.should_force_flatten(during) is True
    assert cal.should_force_flatten(after_close) is False


def test_no_force_flatten_on_holiday():
    cal = _calendar()
    saturday = datetime(2024, 1, 6, 14, 56, tzinfo=JST)
    assert cal.should_force_flatten(saturday) is False

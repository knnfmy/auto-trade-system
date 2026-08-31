"""東証の営業日・立会時間の簡易判定。

jpholiday(日本の祝日判定ライブラリ)が利用可能ならそれを使い、土日・祝日・
年末年始(12/31-1/3)を休場日として扱う。jpholiday が無い環境でも動くよう、
未インストール時は土日のみで判定する(祝日を営業日と誤判定しうるため、
本番運用前に jpholiday のインストールを強く推奨、と README に明記)。
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

try:
    import jpholiday  # type: ignore
except ImportError:  # pragma: no cover - フォールバック経路
    jpholiday = None

JST = ZoneInfo("Asia/Tokyo")


def is_year_end_new_year(d: date) -> bool:
    return (d.month == 12 and d.day == 31) or (d.month == 1 and d.day <= 3)


def is_trading_day(d: date) -> bool:
    if d.weekday() >= 5:  # 土日
        return False
    if is_year_end_new_year(d):
        return False
    if jpholiday is not None and jpholiday.is_holiday(d):
        return False
    return True


class TradingCalendar:
    def __init__(
        self,
        session_start: time,
        session_end: time,
        force_flatten_time: time,
        tz: ZoneInfo = JST,
    ) -> None:
        self.session_start = session_start
        self.session_end = session_end
        self.force_flatten_time = force_flatten_time
        self.tz = tz

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def is_trading_day(self, when: Optional[datetime] = None) -> bool:
        when = when or self.now()
        return is_trading_day(when.date())

    def is_market_open(self, when: Optional[datetime] = None) -> bool:
        when = when or self.now()
        if not self.is_trading_day(when):
            return False
        t = when.timetz().replace(tzinfo=None)
        return self.session_start <= t < self.session_end

    def should_force_flatten(self, when: Optional[datetime] = None) -> bool:
        """一般信用デイトレのため、この時刻以降は残建玉を強制返済すべき、という判定。"""
        when = when or self.now()
        if not self.is_trading_day(when):
            return False
        t = when.timetz().replace(tzinfo=None)
        return self.force_flatten_time <= t < self.session_end

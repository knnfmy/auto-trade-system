"""テクニカル指標計算(pandasベース)。"""
from __future__ import annotations

import pandas as pd


def sma(closes: pd.Series, period: int) -> pd.Series:
    return closes.rolling(window=period, min_periods=period).mean()


def ema(closes: pd.Series, period: int) -> pd.Series:
    return closes.ewm(span=period, adjust=False, min_periods=period).mean()


def golden_cross(fast: pd.Series, slow: pd.Series) -> bool:
    """直近バーで fast が slow を下から上に抜けたか(ゴールデンクロス)。"""
    if len(fast) < 2 or len(slow) < 2:
        return False
    if fast.iloc[-2] is None or slow.iloc[-2] is None:
        return False
    prev_diff = fast.iloc[-2] - slow.iloc[-2]
    curr_diff = fast.iloc[-1] - slow.iloc[-1]
    if pd.isna(prev_diff) or pd.isna(curr_diff):
        return False
    return bool(prev_diff <= 0 and curr_diff > 0)


def dead_cross(fast: pd.Series, slow: pd.Series) -> bool:
    """直近バーで fast が slow を上から下に抜けたか(デッドクロス)。"""
    if len(fast) < 2 or len(slow) < 2:
        return False
    prev_diff = fast.iloc[-2] - slow.iloc[-2]
    curr_diff = fast.iloc[-1] - slow.iloc[-1]
    if pd.isna(prev_diff) or pd.isna(curr_diff):
        return False
    return bool(prev_diff >= 0 and curr_diff < 0)

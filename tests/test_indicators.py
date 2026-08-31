import pandas as pd

from autotrade.indicators import dead_cross, golden_cross, sma


def test_sma_basic():
    s = pd.Series([1, 2, 3, 4, 5])
    result = sma(s, 3)
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == 2.0
    assert result.iloc[4] == 4.0


def test_golden_cross_detects_upward_crossing():
    fast = pd.Series([1.0, 3.0])
    slow = pd.Series([2.0, 2.5])
    assert golden_cross(fast, slow) is True
    assert dead_cross(fast, slow) is False


def test_dead_cross_detects_downward_crossing():
    fast = pd.Series([3.0, 1.0])
    slow = pd.Series([2.0, 2.5])
    assert dead_cross(fast, slow) is True
    assert golden_cross(fast, slow) is False


def test_no_cross_when_no_sign_change():
    fast = pd.Series([3.0, 3.5])
    slow = pd.Series([2.0, 2.2])
    assert golden_cross(fast, slow) is False
    assert dead_cross(fast, slow) is False


def test_cross_with_nan_is_false():
    fast = pd.Series([float("nan"), 3.0])
    slow = pd.Series([2.0, 2.5])
    assert golden_cross(fast, slow) is False
    assert dead_cross(fast, slow) is False

"""売買戦略インターフェース。

差し替え可能にするため、戦略は「確定した分足バーの系列」を受け取り
`Signal` を返すだけの純粋な関数的コンポーネントとして定義する
(発注・リスク管理は order_manager / risk 側の責務)。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from autotrade.models import Signal


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def evaluate(self, bars: pd.DataFrame, *, has_open_position: bool, position_side: str | None) -> Signal:
        """確定済みバー(列: open/high/low/close, 時系列昇順)からシグナルを判定する。

        Parameters
        ----------
        bars: 対象銘柄の確定バーのDataFrame。
        has_open_position: 現在この銘柄の建玉を保有しているか。
        position_side: 保有中なら "1"(売建/ショート) か "2"(買建/ロング)、無ければNone。
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def warmup_bars(self) -> int:
        """シグナル判定に最低限必要なバー本数。"""
        raise NotImplementedError

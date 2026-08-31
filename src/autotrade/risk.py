"""リスク管理: 建玉サイズ計算、損切り/利確判定、日次損失ガード、多重発注防止。"""
from __future__ import annotations

import math
from dataclasses import dataclass

from autotrade.config import RiskConfig
from autotrade.models import Side
from autotrade.state_store import StateStore

UNIT_SHARES_DEFAULT = 100  # 日本株の単元株数(多くの銘柄で100株単位)


@dataclass
class RiskDecision:
    allowed: bool
    reason: str = ""


class RiskManager:
    def __init__(self, config: RiskConfig, state_store: StateStore) -> None:
        self.config = config
        self.state_store = state_store

    # ------------------------------------------------------------------
    # 新規建玉の可否
    # ------------------------------------------------------------------
    def daily_loss_limit_hit(self) -> bool:
        pnl_today = self.state_store.realized_pnl_on()
        return pnl_today <= -abs(self.config.max_daily_loss_jpy)

    def can_open_new_position(
        self,
        *,
        symbol: str,
        total_open_positions: int,
        symbol_open_positions: int,
    ) -> RiskDecision:
        if self.daily_loss_limit_hit():
            return RiskDecision(False, "日次最大損失に達したため新規建玉を停止しています。")
        if total_open_positions >= self.config.max_concurrent_positions:
            return RiskDecision(False, "口座全体の最大同時建玉数に達しています。")
        if symbol_open_positions >= self.config.max_positions_per_symbol:
            return RiskDecision(False, f"{symbol} の最大建玉数に達しています。")
        return RiskDecision(True)

    # ------------------------------------------------------------------
    # 建玉サイズ
    # ------------------------------------------------------------------
    def calc_qty(
        self,
        *,
        price: float,
        margin_power_jpy: float,
        unit_shares: int = UNIT_SHARES_DEFAULT,
    ) -> int:
        if price <= 0 or margin_power_jpy <= 0:
            return 0
        capital = margin_power_jpy * (self.config.max_capital_per_trade_pct / 100.0)
        raw_qty = capital / price
        units = math.floor(raw_qty / unit_shares)
        return max(units * unit_shares, 0)

    # ------------------------------------------------------------------
    # 損切り/利確
    # ------------------------------------------------------------------
    def stop_loss_price(self, entry_price: float, side: Side) -> float:
        pct = self.config.stop_loss_pct / 100.0
        if side is Side.BUY:  # 買建(ロング): 下落したら損切り
            return entry_price * (1 - pct)
        return entry_price * (1 + pct)  # 売建(ショート): 上昇したら損切り

    def take_profit_price(self, entry_price: float, side: Side) -> float:
        pct = self.config.take_profit_pct / 100.0
        if side is Side.BUY:
            return entry_price * (1 + pct)
        return entry_price * (1 - pct)

    def check_exit_by_price(self, *, entry_price: float, current_price: float, side: Side) -> RiskDecision:
        sl = self.stop_loss_price(entry_price, side)
        tp = self.take_profit_price(entry_price, side)
        if side is Side.BUY:
            if current_price <= sl:
                return RiskDecision(True, f"損切り(買建): {current_price} <= {sl:.1f}")
            if current_price >= tp:
                return RiskDecision(True, f"利確(買建): {current_price} >= {tp:.1f}")
        else:
            if current_price >= sl:
                return RiskDecision(True, f"損切り(売建): {current_price} >= {sl:.1f}")
            if current_price <= tp:
                return RiskDecision(True, f"利確(売建): {current_price} <= {tp:.1f}")
        return RiskDecision(False)

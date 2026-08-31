from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from autotrade.config import RiskConfig
from autotrade.models import Side
from autotrade.risk import RiskManager
from autotrade.state_store import StateStore

JST = ZoneInfo("Asia/Tokyo")


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


@pytest.fixture
def risk(store):
    config = RiskConfig(
        max_capital_per_trade_pct=10.0,
        max_concurrent_positions=2,
        max_positions_per_symbol=1,
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
        max_daily_loss_jpy=10000.0,
        entry_order_timeout_sec=30,
    )
    return RiskManager(config, store)


def test_calc_qty_rounds_down_to_unit_shares(risk):
    # margin_power=1,000,000円, max_capital_per_trade_pct=10% -> 100,000円
    # price=1,234円 -> 81.0株 -> 単元(100株)切り捨てで 0株
    qty = risk.calc_qty(price=1234, margin_power_jpy=1_000_000)
    assert qty == 0

    qty = risk.calc_qty(price=500, margin_power_jpy=1_000_000)
    # 100,000 / 500 = 200株 -> 100株単位で200株
    assert qty == 200


def test_calc_qty_zero_when_no_margin_power(risk):
    assert risk.calc_qty(price=500, margin_power_jpy=0) == 0


def test_can_open_new_position_blocks_on_concurrent_limit(risk):
    decision = risk.can_open_new_position(symbol="7203", total_open_positions=2, symbol_open_positions=0)
    assert decision.allowed is False


def test_can_open_new_position_blocks_on_symbol_limit(risk):
    decision = risk.can_open_new_position(symbol="7203", total_open_positions=0, symbol_open_positions=1)
    assert decision.allowed is False


def test_can_open_new_position_allows_within_limits(risk):
    decision = risk.can_open_new_position(symbol="7203", total_open_positions=0, symbol_open_positions=0)
    assert decision.allowed is True


def test_daily_loss_guard_blocks_new_entries(risk, store):
    trade_id = store.record_entry(symbol="7203", side="2", entry_order_id="o1", qty=100, entry_price=1000)
    store.record_exit(trade_id, exit_order_id="c1", exit_price=850, realized_pnl=-15000)

    assert risk.daily_loss_limit_hit() is True
    decision = risk.can_open_new_position(symbol="9433", total_open_positions=0, symbol_open_positions=0)
    assert decision.allowed is False


def test_stop_loss_and_take_profit_prices_for_long(risk):
    sl = risk.stop_loss_price(1000, Side.BUY)
    tp = risk.take_profit_price(1000, Side.BUY)
    assert sl == pytest.approx(985.0)
    assert tp == pytest.approx(1030.0)


def test_stop_loss_and_take_profit_prices_for_short(risk):
    sl = risk.stop_loss_price(1000, Side.SELL)
    tp = risk.take_profit_price(1000, Side.SELL)
    assert sl == pytest.approx(1015.0)
    assert tp == pytest.approx(970.0)


def test_check_exit_by_price_long_stop_loss(risk):
    decision = risk.check_exit_by_price(entry_price=1000, current_price=980, side=Side.BUY)
    assert decision.allowed is True
    assert "損切り" in decision.reason


def test_check_exit_by_price_long_take_profit(risk):
    decision = risk.check_exit_by_price(entry_price=1000, current_price=1035, side=Side.BUY)
    assert decision.allowed is True
    assert "利確" in decision.reason


def test_check_exit_by_price_within_band_no_exit(risk):
    decision = risk.check_exit_by_price(entry_price=1000, current_price=1005, side=Side.BUY)
    assert decision.allowed is False

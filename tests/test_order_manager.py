import pytest

from autotrade.config import RiskConfig
from autotrade.models import AccountType, Exchange, Signal, Side
from autotrade.order_manager import OrderManager
from autotrade.risk import RiskManager
from autotrade.state_store import StateStore


class FakeOrderResult:
    def __init__(self, order_id):
        self.order_id = order_id


class FakeKabuClient:
    def __init__(self):
        self.sent_orders: list[dict] = []
        self.canceled_orders: list[str] = []
        self._orders_response: list[dict] = []
        self._next_order_id = 1

    def send_order(self, payload):
        self.sent_orders.append(payload)
        order_id = f"ORDER{self._next_order_id}"
        self._next_order_id += 1
        return FakeOrderResult(order_id)

    def cancel_order(self, order_id):
        self.canceled_orders.append(order_id)
        return {}

    def get_orders(self, **kwargs):
        return self._orders_response

    def set_orders_response(self, orders):
        self._orders_response = orders


def fill_detail(price, qty, execution_id):
    return {"RecType": 8, "Price": price, "Qty": qty, "ExecutionID": execution_id}


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


@pytest.fixture
def risk(store):
    config = RiskConfig(
        max_capital_per_trade_pct=10.0,
        max_concurrent_positions=5,
        max_positions_per_symbol=1,
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
        max_daily_loss_jpy=100000.0,
        entry_order_timeout_sec=30,
    )
    return RiskManager(config, store)


@pytest.fixture
def client():
    return FakeKabuClient()


@pytest.fixture
def manager(client, risk, store):
    return OrderManager(
        client=client,
        risk=risk,
        state_store=store,
        account_type=AccountType.SPECIFIC,
        entry_order_timeout_sec=30,
        dry_run=False,
    )


def test_open_position_sends_margin_open_payload(manager, client):
    trade_id = manager.open_position(
        symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0
    )
    assert trade_id is not None
    payload = client.sent_orders[-1]
    assert payload["CashMargin"] == 2  # 新規
    assert payload["MarginTradeType"] == 3  # 一般信用デイトレ
    assert payload["Side"] == "2"
    assert payload["Qty"] == 100
    assert payload["FrontOrderType"] == 20  # 指値


def test_open_position_blocked_by_risk(manager, client, store):
    trade_id_1 = manager.open_position(
        symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0
    )
    # 銘柄あたり最大建玉数(1)に達しているため2件目はブロックされる
    trade_id_2 = manager.open_position(
        symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0
    )
    assert trade_id_1 is not None
    assert trade_id_2 is None
    assert len(client.sent_orders) == 1


def test_entry_fill_promotes_to_tracked_position(manager, client):
    trade_id = manager.open_position(
        symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0
    )
    order_id = list(manager._pending_entries.keys())[0]

    client.set_orders_response(
        [
            {
                "ID": order_id,
                "State": 5,
                "Details": [fill_detail(2498.0, 100, "E-HOLD-1")],
            }
        ]
    )
    manager.sync()

    assert order_id not in manager._pending_entries
    tracked = manager.open_tracked_positions(symbol="7203")
    assert len(tracked) == 1
    assert tracked[0].hold_id == "E-HOLD-1"
    assert tracked[0].entry_price == 2498.0
    assert trade_id == tracked[0].trade_id


def test_entry_without_fill_marks_canceled(manager, client, store):
    manager.open_position(symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0)
    order_id = list(manager._pending_entries.keys())[0]

    client.set_orders_response([{"ID": order_id, "State": 5, "Details": []}])
    manager.sync()

    assert manager.open_tracked_positions() == []
    assert store.open_trades() == []


def test_close_position_then_fill_records_realized_pnl(manager, client, store):
    manager.open_position(symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0)
    entry_order_id = list(manager._pending_entries.keys())[0]
    client.set_orders_response(
        [{"ID": entry_order_id, "State": 5, "Details": [fill_detail(2500.0, 100, "E-HOLD-1")]}]
    )
    manager.sync()

    tracked = manager.open_tracked_positions()[0]
    close_order_id = manager.close_position(tracked, exchange=Exchange.TOKYO, reason="test")
    assert close_order_id is not None

    close_payload = client.sent_orders[-1]
    assert close_payload["CashMargin"] == 3  # 返済
    assert close_payload["ClosePositions"] == [{"HoldID": "E-HOLD-1", "Qty": 100}]

    client.set_orders_response(
        [{"ID": close_order_id, "State": 5, "Details": [fill_detail(2560.0, 100, "E-HOLD-1")]}]
    )
    manager.sync()

    assert manager.open_tracked_positions() == []
    assert store.realized_pnl_on() == pytest.approx((2560.0 - 2500.0) * 100)


def test_force_flatten_cancels_pending_and_closes_positions(manager, client):
    manager.open_position(symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0)
    entry_order_id = list(manager._pending_entries.keys())[0]
    client.set_orders_response(
        [{"ID": entry_order_id, "State": 5, "Details": [fill_detail(2500.0, 100, "E-HOLD-1")]}]
    )
    manager.sync()

    manager.open_position(symbol="9433", exchange=Exchange.TOKYO, side=Side.SELL, qty=100, limit_price=4000.0)
    pending_order_id = list(manager._pending_entries.keys())[0]

    manager.force_flatten_all({"7203": Exchange.TOKYO, "9433": Exchange.TOKYO})

    assert pending_order_id in client.canceled_orders
    close_payloads = [o for o in client.sent_orders if o.get("CashMargin") == 3]
    assert len(close_payloads) == 1
    assert close_payloads[0]["Symbol"] == "7203"

    # 2回呼んでも多重発注しない
    manager.force_flatten_all({"7203": Exchange.TOKYO, "9433": Exchange.TOKYO})
    assert len([o for o in client.sent_orders if o.get("CashMargin") == 3]) == 1


def test_has_open_or_pending(manager):
    assert manager.has_open_or_pending("7203") == (False, None)
    manager.open_position(symbol="7203", exchange=Exchange.TOKYO, side=Side.BUY, qty=100, limit_price=2500.0)
    has_pos, side = manager.has_open_or_pending("7203")
    assert has_pos is True
    assert side == "2"

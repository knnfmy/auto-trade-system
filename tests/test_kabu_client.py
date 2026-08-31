import json

import pytest

from autotrade.kabu_client import (
    KabuApiError,
    KabuClient,
    TransientKabuApiError,
    _raise_for_response,
)
from autotrade.models import Exchange, SymbolSpec


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.content = json.dumps(payload).encode() if payload is not None else text.encode()
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """呼び出し内容を記録しつつ、事前に登録したレスポンスを順に返すフェイクSession。"""

    def __init__(self):
        self.calls: list[dict] = []
        self._responses: dict[tuple[str, str], list[FakeResponse]] = {}

    def queue(self, method: str, path_suffix: str, response: FakeResponse):
        key = (method.upper(), path_suffix)
        self._responses.setdefault(key, []).append(response)

    def post(self, url, json=None, timeout=None):
        return self.request("POST", url, json=json, timeout=timeout)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json, "headers": headers})
        for (m, suffix), queue in self._responses.items():
            if m == method.upper() and url.endswith(suffix) and queue:
                return queue.pop(0)
        raise AssertionError(f"未登録のリクエスト: {method} {url}")


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def client(session):
    return KabuClient(base_url="http://localhost:18081/kabusapi", api_password="pw", session=session)


def test_raise_for_response_ok_does_nothing():
    _raise_for_response(FakeResponse(200, {"ok": True}))  # 例外が出なければOK


def test_raise_for_response_400_raises_kabu_api_error():
    with pytest.raises(KabuApiError):
        _raise_for_response(FakeResponse(400, {"Code": 4001, "Message": "bad"}))


def test_raise_for_response_429_raises_transient_error():
    with pytest.raises(TransientKabuApiError):
        _raise_for_response(FakeResponse(429, {"Message": "too many"}))


def test_get_token_success(client, session):
    session.queue("POST", "/token", FakeResponse(200, {"ResultCode": 0, "Token": "TOKEN123"}))
    token = client.get_token()
    assert token == "TOKEN123"
    assert session.calls[0]["json"] == {"APIPassword": "pw"}


def test_send_order_uses_token_header(client, session):
    session.queue("POST", "/token", FakeResponse(200, {"Token": "TOKEN123"}))
    session.queue("POST", "/sendorder", FakeResponse(200, {"Result": 0, "OrderId": "ORDER1"}))

    result = client.send_order({"Symbol": "7203"})

    assert result.order_id == "ORDER1"
    sendorder_call = session.calls[-1]
    assert sendorder_call["headers"]["X-API-KEY"] == "TOKEN123"


def test_401_triggers_token_refresh_and_retry(client, session):
    session.queue("POST", "/token", FakeResponse(200, {"Token": "OLD"}))
    session.queue("POST", "/sendorder", FakeResponse(401, {"Message": "unauthorized"}))
    session.queue("POST", "/token", FakeResponse(200, {"Token": "NEW"}))
    session.queue("POST", "/sendorder", FakeResponse(200, {"OrderId": "ORDER2"}))

    result = client.send_order({"Symbol": "7203"})

    assert result.order_id == "ORDER2"
    assert client._token == "NEW"


def test_get_positions_parses_models(client, session):
    session.queue("POST", "/token", FakeResponse(200, {"Token": "T"}))
    session.queue(
        "GET",
        "/positions",
        FakeResponse(
            200,
            [
                {
                    "ExecutionID": "E1",
                    "Symbol": "7203",
                    "SymbolName": "トヨタ自動車",
                    "Exchange": 1,
                    "Side": "2",
                    "Price": 2500.0,
                    "LeavesQty": 100,
                    "HoldQty": 0,
                    "MarginTradeType": 3,
                }
            ],
        ),
    )
    positions = client.get_positions()
    assert len(positions) == 1
    assert positions[0].execution_id == "E1"
    assert positions[0].closable_qty == 100


def test_register_symbols_payload(client, session):
    session.queue("POST", "/token", FakeResponse(200, {"Token": "T"}))
    session.queue("PUT", "/register", FakeResponse(200, {"RegistList": []}))
    client.register_symbols([SymbolSpec(symbol="7203", exchange=Exchange.TOKYO)])
    call = session.calls[-1]
    assert call["json"] == {"Symbols": [{"Symbol": "7203", "Exchange": 1}]}

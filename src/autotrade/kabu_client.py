"""kabuステーションAPI REST クライアント。

エンドポイント仕様は kabucom/kabusapi (公式) の
reference/kabu_STATION_API.yaml に準拠。本システムでは信用取引のみ使用するため
先物・オプション関連のエンドポイントは実装しない。
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from autotrade.models import ClosePositionTarget, OrderResult, Position, SymbolSpec
from autotrade.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)


class KabuApiError(RuntimeError):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body
        super().__init__(f"kabuステーションAPI エラー (HTTP {status_code}): {body}")


class TransientKabuApiError(KabuApiError):
    """429/5xx など、リトライして良いエラー。"""


def _raise_for_response(resp: requests.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    if resp.status_code in (429, 500, 502, 503, 504):
        raise TransientKabuApiError(resp.status_code, body)
    raise KabuApiError(resp.status_code, body)


class KabuClient:
    def __init__(
        self,
        base_url: str,
        api_password: str,
        session: Optional[requests.Session] = None,
        rate_limiter: Optional[RateLimiter] = None,
        order_rate_limiter: Optional[RateLimiter] = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_password = api_password
        self._session = session or requests.Session()
        # 公式の流量制限: 発注系(/sendorder, /cancelorder)は5件/秒、それ以外は10件/秒。
        # 安全マージンを取り、それぞれ少し低めの値をデフォルトにする。
        self._rate_limiter = rate_limiter or RateLimiter(max_per_sec=8.0)
        self._order_rate_limiter = order_rate_limiter or RateLimiter(max_per_sec=4.0)
        self._timeout = timeout
        self._token: Optional[str] = None
        self._token_lock = threading.Lock()

    # ------------------------------------------------------------------
    # トークン管理
    # ------------------------------------------------------------------
    def get_token(self, force: bool = False) -> str:
        with self._token_lock:
            if self._token and not force:
                return self._token
            resp = self._session.post(
                f"{self.base_url}/token",
                json={"APIPassword": self._api_password},
                timeout=self._timeout,
            )
            _raise_for_response(resp)
            data = resp.json()
            token = data.get("Token")
            if not token:
                raise KabuApiError(resp.status_code, data)
            self._token = token
            logger.info("kabuステーションAPI トークンを取得しました。")
            return token

    # ------------------------------------------------------------------
    # 内部リクエストヘルパ
    # ------------------------------------------------------------------
    @retry(
        retry=retry_if_exception_type(TransientKabuApiError),
        wait=wait_exponential(multiplier=1, min=1, max=15),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        _retried_auth: bool = False,
    ) -> Any:
        if path.startswith("/sendorder") or path.startswith("/cancelorder"):
            self._order_rate_limiter.acquire()
        else:
            self._rate_limiter.acquire()
        token = self.get_token()
        headers = {"X-API-KEY": token}
        resp = self._session.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            json=json_body,
            headers=headers,
            timeout=self._timeout,
        )
        if resp.status_code == 401 and not _retried_auth:
            # トークン失効(kabuステーション再ログイン等)。1回だけ再取得してリトライ。
            logger.warning("トークンが無効なため再取得します。")
            self.get_token(force=True)
            return self._request(
                method, path, params=params, json_body=json_body, _retried_auth=True
            )
        _raise_for_response(resp)
        if not resp.content:
            return {}
        return resp.json()

    # ------------------------------------------------------------------
    # 発注
    # ------------------------------------------------------------------
    def send_order(self, payload: dict) -> OrderResult:
        data = self._request("POST", "/sendorder", json_body=payload)
        order_id = data.get("OrderId") or data.get("OrderID")
        if not order_id:
            raise KabuApiError(200, data)
        return OrderResult(order_id=order_id, raw=data)

    def cancel_order(self, order_id: str) -> dict:
        return self._request("POST", "/cancelorder", json_body={"OrderId": order_id})

    # ------------------------------------------------------------------
    # 参照系
    # ------------------------------------------------------------------
    def get_orders(
        self,
        *,
        product: Optional[str] = None,
        symbol: Optional[str] = None,
        state: Optional[str] = None,
    ) -> list[dict]:
        params: dict[str, str] = {}
        if product is not None:
            params["product"] = product
        if symbol is not None:
            params["symbol"] = symbol
        if state is not None:
            params["state"] = state
        data = self._request("GET", "/orders", params=params)
        return data or []

    def get_positions(self, *, product: str = "2", symbol: Optional[str] = None) -> list[Position]:
        """product=2(信用)の建玉一覧を取得する(デフォルト)。"""
        params: dict[str, str] = {"product": product}
        if symbol is not None:
            params["symbol"] = symbol
        data = self._request("GET", "/positions", params=params)
        return [Position.from_api(item) for item in (data or [])]

    def get_wallet_margin(self, symbol: Optional[str] = None) -> dict:
        path = f"/wallet/margin/{symbol}" if symbol else "/wallet/margin"
        return self._request("GET", path)

    def get_board(self, symbol: str, exchange: int) -> dict:
        return self._request("GET", f"/board/{symbol}@{exchange}")

    def get_regulations(self, symbol: str, exchange: int) -> dict:
        return self._request("GET", f"/regulations/{symbol}@{exchange}")

    # ------------------------------------------------------------------
    # 銘柄登録(PUSH配信用)
    # ------------------------------------------------------------------
    def register_symbols(self, symbols: list[SymbolSpec]) -> dict:
        payload = {
            "Symbols": [
                {"Symbol": s.symbol, "Exchange": int(s.exchange)} for s in symbols
            ]
        }
        return self._request("PUT", "/register", json_body=payload)

    def unregister_all(self) -> dict:
        return self._request("PUT", "/unregister/all")


def build_close_positions_payload(targets: list[ClosePositionTarget]) -> list[dict]:
    return [{"HoldID": t.hold_id, "Qty": t.qty} for t in targets]

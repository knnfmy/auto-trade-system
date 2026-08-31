"""シグナル→発注への変換、注文/建玉状態の同期、SL/TP監視、強制フラットを担う。

kabuステーションAPIの制約:
- 発注(sendorder)のレスポンスは OrderId のみで、約定結果はPUSH配信されない。
  → /orders をポーリングして State/Details から約定・失効を判定する。
- /orders の各注文には Details(明細)配列があり、RecType=8(約定)の明細に
  約定価格(Price)・数量(Qty)・ExecutionID が入る。信用新規の約定明細の
  ExecutionID がそのまま返済時に指定する HoldID になるため、これを直接使う
  (建玉一覧とのつき合わせによる推測は行わない)。
- 本システムが発注した注文以外(kabuステーション画面から手動発注・返済した分)
  は追跡対象外。手動操作を混在させると実現損益の記録がずれるため避けること。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from autotrade.kabu_client import KabuClient, build_close_positions_payload
from autotrade.models import (
    AccountType,
    CashMargin,
    ClosePositionTarget,
    DelivType,
    Exchange,
    FrontOrderType,
    MarginTradeType,
    SecurityType,
    Side,
    Signal,
)
from autotrade.risk import RiskManager
from autotrade.state_store import StateStore

logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")


@dataclass
class PendingEntry:
    trade_id: int
    order_id: str
    symbol: str
    side: Side
    qty: float
    limit_price: float
    sent_at: datetime


@dataclass
class TrackedPosition:
    trade_id: int
    symbol: str
    side: Side
    qty: float
    entry_price: float
    hold_id: Optional[str] = None


@dataclass
class PendingClose:
    trade_id: int
    order_id: str
    tracked: TrackedPosition
    sent_at: datetime


def _extract_fill(order: dict) -> Optional[tuple[float, str]]:
    """/orders の1件から約定明細(RecType=8)を集計し、(加重平均約定価格, 最後のExecutionID)を返す。

    約定明細が無ければ None(未約定・取消・失効等)。
    """
    details = order.get("Details") or []
    fills = [d for d in details if d.get("RecType") == 8 and d.get("ExecutionID")]
    if not fills:
        return None
    total_qty = sum(float(d.get("Qty", 0) or 0) for d in fills)
    if total_qty <= 0:
        return None
    weighted_price = sum(float(d.get("Price", 0) or 0) * float(d.get("Qty", 0) or 0) for d in fills) / total_qty
    last_execution_id = fills[-1]["ExecutionID"]
    return weighted_price, last_execution_id


class OrderManager:
    def __init__(
        self,
        client: KabuClient,
        risk: RiskManager,
        state_store: StateStore,
        account_type: AccountType,
        entry_order_timeout_sec: int,
        dry_run: bool = False,
    ) -> None:
        self.client = client
        self.risk = risk
        self.state_store = state_store
        self.account_type = account_type
        self.entry_order_timeout_sec = entry_order_timeout_sec
        self.dry_run = dry_run

        self._pending_entries: dict[str, PendingEntry] = {}  # order_id -> PendingEntry
        self._positions: dict[int, TrackedPosition] = {}  # trade_id -> TrackedPosition
        self._pending_closes: dict[str, PendingClose] = {}  # order_id -> PendingClose
        self._flatten_triggered = False

    # ------------------------------------------------------------------
    # 発注ヘルパ
    # ------------------------------------------------------------------
    def _send_order(self, payload: dict) -> Optional[str]:
        if self.dry_run:
            logger.info("[DRY-RUN] sendorder payload=%s", payload)
            return f"DRYRUN-{datetime.now(JST).strftime('%H%M%S%f')}"
        result = self.client.send_order(payload)
        return result.order_id

    def _base_payload(self, symbol: str, exchange: Exchange) -> dict:
        return {
            "Symbol": symbol,
            "Exchange": int(exchange),
            "SecurityType": int(SecurityType.STOCK),
            "AccountType": int(self.account_type),
        }

    # ------------------------------------------------------------------
    # 新規建玉
    # ------------------------------------------------------------------
    def open_position(
        self,
        *,
        symbol: str,
        exchange: Exchange,
        side: Side,
        qty: int,
        limit_price: float,
    ) -> Optional[int]:
        """信用新規注文を送信し、内部の trade_id を返す(拒否/エラー時は None)。"""
        total_open = len(self._positions) + len(self._pending_entries)
        symbol_open = sum(
            1
            for p in list(self._positions.values()) + list(self._pending_entries.values())
            if p.symbol == symbol
        )
        decision = self.risk.can_open_new_position(
            symbol=symbol, total_open_positions=total_open, symbol_open_positions=symbol_open
        )
        if not decision.allowed:
            logger.info("新規建玉を見送り: %s (%s)", symbol, decision.reason)
            return None
        if qty <= 0:
            logger.info("発注数量が0のため新規建玉を見送り: %s", symbol)
            return None

        payload = self._base_payload(symbol, exchange)
        payload.update(
            {
                "Side": side.value,
                "CashMargin": int(CashMargin.MARGIN_OPEN),
                "MarginTradeType": int(MarginTradeType.IPPAN_DAY_TRADE),
                "DelivType": int(DelivType.UNSPECIFIED),
                "Qty": qty,
                "FrontOrderType": int(FrontOrderType.LIMIT),
                "Price": limit_price,
                "ExpireDay": 0,
            }
        )
        order_id = self._send_order(payload)
        if order_id is None:
            return None

        trade_id = self.state_store.record_entry(
            symbol=symbol, side=side.value, entry_order_id=order_id, qty=qty, entry_price=limit_price
        )
        self._pending_entries[order_id] = PendingEntry(
            trade_id=trade_id,
            order_id=order_id,
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            sent_at=datetime.now(JST),
        )
        logger.info("新規建玉を発注: symbol=%s side=%s qty=%s price=%s order_id=%s", symbol, side, qty, limit_price, order_id)
        return trade_id

    def open_position_for_signal(
        self,
        *,
        signal: Signal,
        symbol: str,
        exchange: Exchange,
        current_price: float,
        margin_power_jpy: float,
    ) -> Optional[int]:
        if signal not in (Signal.ENTRY_LONG, Signal.ENTRY_SHORT):
            return None
        side = Side.BUY if signal is Signal.ENTRY_LONG else Side.SELL
        qty = self.risk.calc_qty(price=current_price, margin_power_jpy=margin_power_jpy)
        return self.open_position(symbol=symbol, exchange=exchange, side=side, qty=qty, limit_price=current_price)

    # ------------------------------------------------------------------
    # 手仕舞い(信用返済)
    # ------------------------------------------------------------------
    def close_position(
        self, tracked: TrackedPosition, *, exchange: Exchange, reason: str, market: bool = True
    ) -> Optional[str]:
        if tracked.hold_id is None:
            logger.warning("建玉ID未確定のため手仕舞いを保留: trade_id=%s symbol=%s", tracked.trade_id, tracked.symbol)
            return None
        if any(pc.trade_id == tracked.trade_id for pc in self._pending_closes.values()):
            logger.debug("既に返済注文を発注済みのためスキップ: trade_id=%s", tracked.trade_id)
            return None

        payload = self._base_payload(tracked.symbol, exchange)
        payload.update(
            {
                "Side": tracked.side.opposite.value,
                "CashMargin": int(CashMargin.MARGIN_CLOSE),
                "MarginTradeType": int(MarginTradeType.IPPAN_DAY_TRADE),
                "DelivType": int(DelivType.UNSPECIFIED),
                "Qty": tracked.qty,
                "ClosePositions": build_close_positions_payload(
                    [ClosePositionTarget(hold_id=tracked.hold_id, qty=tracked.qty)]
                ),
                "FrontOrderType": int(FrontOrderType.MARKET) if market else int(FrontOrderType.LIMIT),
                "Price": 0 if market else tracked.entry_price,
                "ExpireDay": 0,
            }
        )
        order_id = self._send_order(payload)
        if order_id is None:
            return None
        self._pending_closes[order_id] = PendingClose(
            trade_id=tracked.trade_id, order_id=order_id, tracked=tracked, sent_at=datetime.now(JST)
        )
        logger.info(
            "返済注文を発注: trade_id=%s symbol=%s qty=%s hold_id=%s reason=%s order_id=%s",
            tracked.trade_id, tracked.symbol, tracked.qty, tracked.hold_id, reason, order_id,
        )
        return order_id

    def close_symbol(self, symbol: str, *, exchange: Exchange, reason: str, market: bool = True) -> None:
        for tracked in [p for p in self._positions.values() if p.symbol == symbol]:
            self.close_position(tracked, exchange=exchange, reason=reason, market=market)

    def force_flatten_all(self, symbol_to_exchange: dict[str, Exchange]) -> None:
        if self._flatten_triggered:
            return
        self._flatten_triggered = True
        logger.warning("強制フラット時刻のため全建玉の返済を発注します。")
        for order_id, pending in list(self._pending_entries.items()):
            if not self.dry_run:
                try:
                    self.client.cancel_order(order_id)
                except Exception:  # noqa: BLE001
                    logger.exception("未約定の新規注文取消に失敗: order_id=%s", order_id)
            self.state_store.mark_canceled(pending.trade_id)
            del self._pending_entries[order_id]
        for tracked in list(self._positions.values()):
            exchange = symbol_to_exchange.get(tracked.symbol, Exchange.TOKYO)
            self.close_position(tracked, exchange=exchange, reason="強制フラット(デイトレのため引け前手仕舞い)", market=True)

    def reset_daily_state(self) -> None:
        """新しい取引日の開始時に呼び出す。"""
        self._flatten_triggered = False

    # ------------------------------------------------------------------
    # 状態同期
    # ------------------------------------------------------------------
    def sync(self) -> None:
        orders_by_id = self._fetch_orders()
        if orders_by_id is None:
            return
        self._sync_pending_entries(orders_by_id)
        self._sync_pending_closes(orders_by_id)

    def _fetch_orders(self) -> Optional[dict[str, dict]]:
        if not self._pending_entries and not self._pending_closes:
            return {}
        try:
            return {o.get("ID"): o for o in self.client.get_orders(product="2")}
        except Exception:  # noqa: BLE001
            logger.exception("注文照会に失敗しました。")
            return None

    def _sync_pending_entries(self, orders_by_id: dict[str, dict]) -> None:
        if not self._pending_entries:
            return
        now = datetime.now(JST)

        for order_id, pending in list(self._pending_entries.items()):
            order = orders_by_id.get(order_id)
            if self.dry_run and order is None:
                # ドライランでは実際の注文は存在しないため、発注直後に約定したものとして扱う。
                self._promote_to_tracked(
                    pending, fill_price=pending.limit_price, hold_id=f"DRYRUN-HOLD-{order_id}"
                )
                del self._pending_entries[order_id]
                continue
            if order is None:
                continue

            fill = _extract_fill(order)
            if fill is not None:
                fill_price, hold_id = fill
                self._promote_to_tracked(pending, fill_price=fill_price, hold_id=hold_id)
                del self._pending_entries[order_id]
                continue

            if order.get("State") == 5:  # 終了(約定明細が無い=未約定のまま失効/取消/エラー)
                logger.info("新規注文が未約定のまま終了: order_id=%s", order_id)
                self.state_store.mark_canceled(pending.trade_id)
                del self._pending_entries[order_id]
                continue

            elapsed = (now - pending.sent_at).total_seconds()
            if elapsed >= self.entry_order_timeout_sec:
                logger.info("新規注文がタイムアウトのため取消: order_id=%s elapsed=%.0fs", order_id, elapsed)
                try:
                    self.client.cancel_order(order_id)
                except Exception:  # noqa: BLE001
                    logger.exception("注文取消に失敗: order_id=%s", order_id)

    def _promote_to_tracked(self, pending: PendingEntry, fill_price: Optional[float], hold_id: str) -> None:
        tracked = TrackedPosition(
            trade_id=pending.trade_id,
            symbol=pending.symbol,
            side=pending.side,
            qty=pending.qty,
            entry_price=fill_price or 0.0,
            hold_id=hold_id,
        )
        self._positions[pending.trade_id] = tracked
        self.state_store.set_hold_id(pending.trade_id, hold_id)
        logger.info(
            "新規建玉が約定しました: trade_id=%s symbol=%s qty=%s price=%s hold_id=%s",
            tracked.trade_id, tracked.symbol, tracked.qty, tracked.entry_price, hold_id,
        )

    def _sync_pending_closes(self, orders_by_id: dict[str, dict]) -> None:
        if not self._pending_closes:
            return
        for order_id, pending in list(self._pending_closes.items()):
            order = orders_by_id.get(order_id)
            fill_price: Optional[float] = None
            if self.dry_run and order is None:
                fill_price = pending.tracked.entry_price
            elif order is not None:
                fill = _extract_fill(order)
                if fill is not None:
                    fill_price = fill[0]
                elif order.get("State") == 5:
                    logger.error(
                        "返済注文が未約定のまま終了しました。建玉が残っている可能性があるため手動で確認してください: "
                        "trade_id=%s order_id=%s",
                        pending.trade_id, order_id,
                    )
                    del self._pending_closes[order_id]
                    continue
                else:
                    continue  # まだ処理中
            else:
                continue  # 注文情報がまだ反映されていない

            tracked = pending.tracked
            if tracked.side is Side.BUY:
                realized_pnl = (fill_price - tracked.entry_price) * tracked.qty
            else:
                realized_pnl = (tracked.entry_price - fill_price) * tracked.qty
            self.state_store.record_exit(
                pending.trade_id, exit_order_id=order_id, exit_price=fill_price, realized_pnl=realized_pnl
            )
            self._positions.pop(pending.trade_id, None)
            del self._pending_closes[order_id]
            logger.info(
                "返済が約定しました: trade_id=%s symbol=%s exit_price=%s realized_pnl=%.0f",
                pending.trade_id, tracked.symbol, fill_price, realized_pnl,
            )

    # ------------------------------------------------------------------
    def open_tracked_positions(self, symbol: Optional[str] = None) -> list[TrackedPosition]:
        values = list(self._positions.values())
        if symbol is None:
            return values
        return [p for p in values if p.symbol == symbol]

    def has_open_or_pending(self, symbol: str) -> tuple[bool, Optional[str]]:
        for p in self._positions.values():
            if p.symbol == symbol:
                return True, p.side.value
        for p in self._pending_entries.values():
            if p.symbol == symbol:
                return True, p.side.value
        return False, None

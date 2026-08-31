"""売買履歴・日次損益を永続化するSQLiteストア。

プロセス再起動をまたいでも「今日はいくら損しているか」を正しく把握できるよう、
実現損益はDBに記録する(日次損失上限のガードに必須)。
"""
from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,           -- '1'=売建(ショート) / '2'=買建(ロング)
    entry_order_id TEXT,
    hold_id TEXT,                 -- 建玉ID(ExecutionID)。約定確認後にセット
    exit_order_id TEXT,
    qty REAL NOT NULL,
    entry_price REAL,
    exit_price REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL,
    status TEXT NOT NULL DEFAULT 'open'  -- open / closed / canceled
);
"""


@dataclass
class Trade:
    id: int
    symbol: str
    side: str
    entry_order_id: Optional[str]
    hold_id: Optional[str]
    exit_order_id: Optional[str]
    qty: float
    entry_price: Optional[float]
    exit_price: Optional[float]
    opened_at: str
    closed_at: Optional[str]
    realized_pnl: Optional[float]
    status: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Trade":
        return cls(**{k: row[k] for k in row.keys()})


class StateStore:
    def __init__(self, db_path: str | Path) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    def record_entry(
        self,
        *,
        symbol: str,
        side: str,
        entry_order_id: str,
        qty: float,
        entry_price: Optional[float] = None,
        opened_at: Optional[datetime] = None,
    ) -> int:
        opened_at = opened_at or datetime.now(JST)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO trades (symbol, side, entry_order_id, qty, entry_price, opened_at, status)
                VALUES (?, ?, ?, ?, ?, ?, 'open')
                """,
                (symbol, side, entry_order_id, qty, entry_price, opened_at.isoformat()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def set_hold_id(self, trade_id: int, hold_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE trades SET hold_id = ? WHERE id = ?", (hold_id, trade_id))
            self._conn.commit()

    def record_exit(
        self,
        trade_id: int,
        *,
        exit_order_id: str,
        exit_price: Optional[float],
        realized_pnl: Optional[float],
        closed_at: Optional[datetime] = None,
    ) -> None:
        closed_at = closed_at or datetime.now(JST)
        with self._lock:
            self._conn.execute(
                """
                UPDATE trades
                SET exit_order_id = ?, exit_price = ?, realized_pnl = ?, closed_at = ?, status = 'closed'
                WHERE id = ?
                """,
                (exit_order_id, exit_price, realized_pnl, closed_at.isoformat(), trade_id),
            )
            self._conn.commit()

    def mark_canceled(self, trade_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE trades SET status = 'canceled' WHERE id = ?", (trade_id,))
            self._conn.commit()

    def open_trades(self, symbol: Optional[str] = None) -> list[Trade]:
        with self._lock:
            if symbol:
                rows = self._conn.execute(
                    "SELECT * FROM trades WHERE status = 'open' AND symbol = ?", (symbol,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM trades WHERE status = 'open'").fetchall()
        return [Trade.from_row(r) for r in rows]

    def realized_pnl_on(self, day: Optional[datetime] = None) -> float:
        """指定日(デフォルト今日、JST)の実現損益合計。"""
        day = day or datetime.now(JST)
        day_str = day.strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COALESCE(SUM(realized_pnl), 0) AS total
                FROM trades
                WHERE status = 'closed' AND substr(closed_at, 1, 10) = ?
                """,
                (day_str,),
            ).fetchone()
        return float(row["total"])

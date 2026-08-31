"""kabuステーションAPIのドメインモデル(Enum/dataclass)。

数値定義は公式リファレンス(kabucom/kabusapi の reference/kabu_STATION_API.yaml)に準拠。
本システムは信用取引(一般信用・デイトレード)専用のため、MarginTradeType は
DAY_TRADE(3) 固定で使う想定だが、値自体は他区分も定義しておく。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Exchange(int, Enum):
    TOKYO = 1
    NAGOYA = 3
    FUKUOKA = 5
    SAPPORO = 6
    SOR = 9
    TOKYO_PLUS = 27


class SecurityType(int, Enum):
    STOCK = 1


class Side(str, Enum):
    SELL = "1"
    BUY = "2"

    @property
    def opposite(self) -> "Side":
        return Side.BUY if self is Side.SELL else Side.SELL


class CashMargin(int, Enum):
    CASH = 1
    MARGIN_OPEN = 2   # 信用新規
    MARGIN_CLOSE = 3  # 信用返済


class MarginTradeType(int, Enum):
    SEIDO = 1              # 制度信用
    IPPAN_LONG = 2         # 一般信用(長期)
    IPPAN_DAY_TRADE = 3    # 一般信用(デイトレ) ※本システムはこれのみ使用


class DelivType(int, Enum):
    UNSPECIFIED = 0
    DEPOSIT = 2
    AU_MONEY_CONNECT = 3


class FundType(str, Enum):
    MARGIN = "11"  # 信用取引(新規・返済とも指定不要だが明示しておく)


class AccountType(int, Enum):
    GENERAL = 2
    SPECIFIC = 4
    CORPORATION = 12


class FrontOrderType(int, Enum):
    MARKET = 10                 # 成行
    MARKET_OPEN_MORNING = 13    # 寄成(前場)
    MARKET_OPEN_AFTERNOON = 14  # 寄成(後場)
    MARKET_CLOSE_MORNING = 15   # 引成(前場)
    MARKET_CLOSE_AFTERNOON = 16  # 引成(後場)
    IOC_MARKET = 17
    LIMIT = 20                  # 指値
    LIMIT_OPEN_MORNING = 21
    LIMIT_OPEN_AFTERNOON = 22
    LIMIT_CLOSE_MORNING = 23
    LIMIT_CLOSE_AFTERNOON = 24
    LIMIT_MARKET_ON_CLOSE_MORNING = 25  # 不成(前場)
    LIMIT_MARKET_ON_CLOSE_AFTERNOON = 26
    IOC_LIMIT = 27
    STOP = 30                   # 逆指値


class OrderState(int, Enum):
    """/orders の State (概略)。値は取得結果の解釈にのみ使用。"""
    WAITING = 1
    SENDING = 2
    PROCESSED = 3
    CANCELING = 4
    DONE = 5


class Signal(Enum):
    NONE = "none"
    ENTRY_LONG = "entry_long"
    ENTRY_SHORT = "entry_short"
    EXIT = "exit"


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    exchange: Exchange = Exchange.TOKYO


@dataclass
class Bar:
    """自前で組み立てる分足バー。"""
    symbol: str
    start: datetime
    open: float
    high: float
    low: float
    close: float
    tick_count: int = 0


@dataclass
class Position:
    """GET /positions の1レコード(信用建玉)。"""
    execution_id: str  # 返済時の HoldID として使う
    symbol: str
    symbol_name: str
    exchange: Exchange
    side: Side
    price: float
    leaves_qty: float
    hold_qty: float
    margin_trade_type: Optional[MarginTradeType]
    current_price: Optional[float] = None
    profit_loss: Optional[float] = None
    profit_loss_rate: Optional[float] = None
    execution_day: Optional[int] = None

    @property
    def closable_qty(self) -> float:
        """返済注文にまだ使える残数量(拘束分を除く)。"""
        return max(self.leaves_qty - self.hold_qty, 0)

    @classmethod
    def from_api(cls, data: dict) -> "Position":
        mtt = data.get("MarginTradeType")
        return cls(
            execution_id=data["ExecutionID"],
            symbol=data["Symbol"],
            symbol_name=data.get("SymbolName", ""),
            exchange=Exchange(data["Exchange"]) if data.get("Exchange") in [e.value for e in Exchange] else Exchange.TOKYO,
            side=Side(data["Side"]),
            price=float(data["Price"]),
            leaves_qty=float(data["LeavesQty"]),
            hold_qty=float(data.get("HoldQty", 0)),
            margin_trade_type=MarginTradeType(mtt) if mtt in [m.value for m in MarginTradeType] else None,
            current_price=data.get("CurrentPrice"),
            profit_loss=data.get("ProfitLoss"),
            profit_loss_rate=data.get("ProfitLossRate"),
            execution_day=data.get("ExecutionDay"),
        )


@dataclass
class OrderResult:
    order_id: str
    raw: dict = field(default_factory=dict)


@dataclass
class ClosePositionTarget:
    hold_id: str
    qty: float

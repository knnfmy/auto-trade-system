"""設定ファイル(YAML) + 環境変数(.env)のロード。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

from autotrade.models import AccountType, Exchange, SymbolSpec


class Environment(str, Enum):
    VERIFICATION = "verification"
    PRODUCTION = "production"

    @property
    def default_port(self) -> int:
        return 18080 if self is Environment.PRODUCTION else 18081


class ConfigError(RuntimeError):
    pass


@dataclass
class KabuStationConfig:
    host: str = "localhost"
    port_override: Optional[int] = None

    def port(self, environment: Environment) -> int:
        return self.port_override or environment.default_port

    def base_url(self, environment: Environment) -> str:
        return f"http://{self.host}:{self.port(environment)}/kabusapi"

    def ws_url(self, environment: Environment) -> str:
        return f"ws://{self.host}:{self.port(environment)}/kabusapi/websocket"


@dataclass
class StrategyConfig:
    name: str = "ma_cross"
    bar_interval_sec: int = 60
    fast_period: int = 5
    slow_period: int = 25
    warmup_bars: int = 30


@dataclass
class RiskConfig:
    max_capital_per_trade_pct: float = 10.0
    max_concurrent_positions: int = 3
    max_positions_per_symbol: int = 1
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 3.0
    max_daily_loss_jpy: float = 30000.0
    entry_order_timeout_sec: int = 30


@dataclass
class ScheduleConfig:
    session_start: time = time(9, 0)
    session_end: time = time(15, 0)
    force_flatten_time: time = time(14, 55)
    poll_interval_sec: int = 5


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/autotrade.log"


@dataclass
class StateConfig:
    db_path: str = "logs/state.db"


@dataclass
class Settings:
    environment: Environment
    confirm_live_trading: bool
    api_password: str
    account_type: AccountType
    watchlist: list[SymbolSpec]
    kabu_station: KabuStationConfig
    strategy: StrategyConfig
    risk: RiskConfig
    schedule: ScheduleConfig
    logging: LoggingConfig
    state: StateConfig

    @property
    def base_url(self) -> str:
        return self.kabu_station.base_url(self.environment)

    @property
    def ws_url(self) -> str:
        return self.kabu_station.ws_url(self.environment)


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def load_settings(
    config_path: str | Path,
    env_override: Optional[str] = None,
    dotenv_path: Optional[str | Path] = None,
) -> Settings:
    """設定YAMLと.envを読み込み Settings を構築する。

    env_override が指定された場合、YAML の environment より優先される
    (例: CLI の `--env production`)。
    """
    load_dotenv(dotenv_path) if dotenv_path else load_dotenv()

    config_path = Path(config_path)
    if not config_path.exists():
        raise ConfigError(
            f"設定ファイルが見つかりません: {config_path}\n"
            "config/config.example.yaml をコピーして作成してください。"
        )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    env_name = env_override or raw.get("environment", "verification")
    try:
        environment = Environment(env_name)
    except ValueError as exc:
        raise ConfigError(f"environment は 'verification' か 'production' を指定してください: {env_name}") from exc

    confirm_live_trading = bool(raw.get("confirm_live_trading", False))
    if environment is Environment.PRODUCTION and not confirm_live_trading:
        raise ConfigError(
            "environment: production を使うには config の confirm_live_trading を true にする必要があります。"
            " 誤発注防止のための安全装置です。検証環境で十分に確認してから切り替えてください。"
        )

    password_env_var = (
        "KABU_API_PASSWORD_PRODUCTION"
        if environment is Environment.PRODUCTION
        else "KABU_API_PASSWORD_VERIFICATION"
    )
    api_password = os.environ.get(password_env_var)
    if not api_password or api_password == "changeme":
        raise ConfigError(
            f"環境変数 {password_env_var} が未設定です。.env.example を参考に .env を作成してください。"
        )

    account_raw = raw.get("account", {})
    account_type = AccountType(account_raw.get("account_type", AccountType.SPECIFIC.value))

    watchlist_raw = raw.get("watchlist", [])
    if not watchlist_raw:
        raise ConfigError("watchlist が空です。少なくとも1銘柄指定してください。")
    watchlist = [
        SymbolSpec(symbol=str(item["symbol"]), exchange=Exchange(item.get("exchange", Exchange.TOKYO.value)))
        for item in watchlist_raw
    ]
    if len(watchlist) > 50:
        raise ConfigError("watchlist は最大50銘柄までです(kabuステーションAPIの登録上限)。")

    ks_raw = raw.get("kabu_station", {})
    kabu_station = KabuStationConfig(
        host=ks_raw.get("host", "localhost"),
        port_override=ks_raw.get("port_override"),
    )

    strategy_raw = raw.get("strategy", {})
    strategy = StrategyConfig(
        name=strategy_raw.get("name", "ma_cross"),
        bar_interval_sec=int(strategy_raw.get("bar_interval_sec", 60)),
        fast_period=int(strategy_raw.get("fast_period", 5)),
        slow_period=int(strategy_raw.get("slow_period", 25)),
        warmup_bars=int(strategy_raw.get("warmup_bars", 30)),
    )
    if strategy.fast_period >= strategy.slow_period:
        raise ConfigError("strategy.fast_period は slow_period より小さくしてください。")

    risk_raw = raw.get("risk", {})
    risk = RiskConfig(
        max_capital_per_trade_pct=float(risk_raw.get("max_capital_per_trade_pct", 10.0)),
        max_concurrent_positions=int(risk_raw.get("max_concurrent_positions", 3)),
        max_positions_per_symbol=int(risk_raw.get("max_positions_per_symbol", 1)),
        stop_loss_pct=float(risk_raw.get("stop_loss_pct", 1.5)),
        take_profit_pct=float(risk_raw.get("take_profit_pct", 3.0)),
        max_daily_loss_jpy=float(risk_raw.get("max_daily_loss_jpy", 30000.0)),
        entry_order_timeout_sec=int(risk_raw.get("entry_order_timeout_sec", 30)),
    )

    sched_raw = raw.get("schedule", {})
    schedule = ScheduleConfig(
        session_start=_parse_time(sched_raw.get("session_start", "09:00")),
        session_end=_parse_time(sched_raw.get("session_end", "15:00")),
        force_flatten_time=_parse_time(sched_raw.get("force_flatten_time", "14:55")),
        poll_interval_sec=int(sched_raw.get("poll_interval_sec", 5)),
    )

    log_raw = raw.get("logging", {})
    logging_cfg = LoggingConfig(
        level=log_raw.get("level", "INFO"),
        file=log_raw.get("file", "logs/autotrade.log"),
    )

    state_raw = raw.get("state", {})
    state_cfg = StateConfig(db_path=state_raw.get("db_path", "logs/state.db"))

    return Settings(
        environment=environment,
        confirm_live_trading=confirm_live_trading,
        api_password=api_password,
        account_type=account_type,
        watchlist=watchlist,
        kabu_station=kabu_station,
        strategy=strategy,
        risk=risk,
        schedule=schedule,
        logging=logging_cfg,
        state=state_cfg,
    )

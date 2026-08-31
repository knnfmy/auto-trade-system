"""エントリポイント。

使い方:
    python -m autotrade.main --config config/config.yaml --env verification --dry-run

必ず検証環境(--env verification)かつ --dry-run で意図通りの発注ログが出ることを
確認してから、--dry-run を外し、最後に本番(--env production)へ切り替えること。
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from datetime import date
from typing import Optional

from autotrade.config import ConfigError, Environment, Settings, load_settings
from autotrade.kabu_client import KabuClient
from autotrade.logging_setup import setup_logging
from autotrade.market_data import MarketDataService
from autotrade.models import Bar, Exchange, Signal
from autotrade.order_manager import OrderManager
from autotrade.rate_limiter import RateLimiter
from autotrade.risk import RiskManager
from autotrade.state_store import StateStore
from autotrade.strategy.ma_cross import MovingAverageCrossStrategy
from autotrade.trading_calendar import TradingCalendar

logger = logging.getLogger(__name__)


def build_strategy(settings: Settings):
    if settings.strategy.name == "ma_cross":
        return MovingAverageCrossStrategy(
            fast_period=settings.strategy.fast_period,
            slow_period=settings.strategy.slow_period,
        )
    raise ConfigError(f"未対応の strategy.name です: {settings.strategy.name}")


class TradingApp:
    def __init__(self, settings: Settings, dry_run: bool) -> None:
        self.settings = settings
        self.dry_run = dry_run

        self.client = KabuClient(
            base_url=settings.base_url,
            api_password=settings.api_password,
            rate_limiter=RateLimiter(max_per_sec=8.0),
        )
        self.state_store = StateStore(settings.state.db_path)
        self.risk = RiskManager(settings.risk, self.state_store)
        self.strategy = build_strategy(settings)
        self.order_manager = OrderManager(
            client=self.client,
            risk=self.risk,
            state_store=self.state_store,
            account_type=settings.account_type,
            entry_order_timeout_sec=settings.risk.entry_order_timeout_sec,
            dry_run=dry_run,
        )
        self.calendar = TradingCalendar(
            session_start=settings.schedule.session_start,
            session_end=settings.schedule.session_end,
            force_flatten_time=settings.schedule.force_flatten_time,
        )
        self.symbol_to_exchange: dict[str, Exchange] = {s.symbol: s.exchange for s in settings.watchlist}

        self.market_data = MarketDataService(
            ws_url=settings.ws_url,
            get_token=self.client.get_token,
            symbols=settings.watchlist,
            bar_interval_sec=settings.strategy.bar_interval_sec,
        )
        self.market_data.add_bar_listener(self._on_bar_closed)

        self._current_trading_day: Optional[date] = None
        self._stop = False

    # ------------------------------------------------------------------
    def _on_bar_closed(self, bar: Bar) -> None:
        try:
            self._handle_bar(bar)
        except Exception:  # noqa: BLE001 - WebSocketスレッドを落とさない
            logger.exception("バー処理中にエラーが発生しました: symbol=%s", bar.symbol)

    def _handle_bar(self, bar: Bar) -> None:
        now = self.calendar.now()
        if not self.calendar.is_market_open(now) or self.calendar.should_force_flatten(now):
            return  # 引け間際・時間外は新規シグナルを無視(force_flattenはメインループ側が処理)

        exchange = self.symbol_to_exchange[bar.symbol]
        df = self.market_data.get_bars_df(bar.symbol)
        has_pos, pos_side = self.order_manager.has_open_or_pending(bar.symbol)
        signal = self.strategy.evaluate(df, has_open_position=has_pos, position_side=pos_side)

        if signal in (Signal.ENTRY_LONG, Signal.ENTRY_SHORT):
            wallet = self.client.get_wallet_margin()
            margin_power = float(wallet.get("MarginAccountWallet") or 0)
            self.order_manager.open_position_for_signal(
                signal=signal,
                symbol=bar.symbol,
                exchange=exchange,
                current_price=bar.close,
                margin_power_jpy=margin_power,
            )
        elif signal is Signal.EXIT:
            self.order_manager.close_symbol(bar.symbol, exchange=exchange, reason="戦略シグナルによる手仕舞い")

    def _check_stop_loss_take_profit(self) -> None:
        for tracked in self.order_manager.open_tracked_positions():
            price = self.market_data.latest_price(tracked.symbol)
            if price is None or not tracked.entry_price:
                continue
            decision = self.risk.check_exit_by_price(
                entry_price=tracked.entry_price, current_price=price, side=tracked.side
            )
            if decision.allowed:
                exchange = self.symbol_to_exchange[tracked.symbol]
                self.order_manager.close_position(tracked, exchange=exchange, reason=decision.reason)

    def _maybe_roll_trading_day(self) -> None:
        today = self.calendar.now().date()
        if self._current_trading_day != today:
            self._current_trading_day = today
            self.order_manager.reset_daily_state()
            logger.info("取引日が切り替わりました: %s", today)

    # ------------------------------------------------------------------
    def run(self) -> None:
        logger.info(
            "起動: environment=%s dry_run=%s watchlist=%s",
            self.settings.environment.value, self.dry_run, [s.symbol for s in self.settings.watchlist],
        )
        # 疎通確認(トークン取得・信用余力取得)
        self.client.get_token()
        wallet = self.client.get_wallet_margin()
        logger.info("信用新規可能額: %s円", wallet.get("MarginAccountWallet"))

        self.client.register_symbols(self.settings.watchlist)
        self.market_data.start()

        try:
            while not self._stop:
                self._maybe_roll_trading_day()
                now = self.calendar.now()

                if not self.calendar.is_trading_day(now):
                    time.sleep(min(self.settings.schedule.poll_interval_sec * 60, 3600))
                    continue

                if self.calendar.should_force_flatten(now):
                    self.order_manager.sync()
                    self.order_manager.force_flatten_all(self.symbol_to_exchange)
                elif self.calendar.is_market_open(now):
                    self.order_manager.sync()
                    self._check_stop_loss_take_profit()
                else:
                    self.market_data.flush_all_bars()

                time.sleep(self.settings.schedule.poll_interval_sec)
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        logger.info("シャットダウンします。")
        self.market_data.stop()
        self.state_store.close()

    def request_stop(self, *_args) -> None:
        self._stop = True


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="kabuステーションAPI 信用取引自動売買")
    parser.add_argument("--config", default="config/config.yaml", help="設定ファイルパス")
    parser.add_argument(
        "--env", choices=[e.value for e in Environment], default=None, help="実行環境(未指定時は設定ファイルの値)"
    )
    parser.add_argument("--dry-run", action="store_true", help="実発注せずログ出力のみ行う")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    try:
        settings = load_settings(args.config, env_override=args.env)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 1

    setup_logging(settings.logging.level, settings.logging.file)

    if settings.environment is Environment.PRODUCTION and not args.dry_run:
        logger.warning(
            "本番環境で実発注を行います。信用取引はレバレッジがあり、想定を超える損失が発生する可能性があります。"
        )

    app = TradingApp(settings, dry_run=args.dry_run)
    signal.signal(signal.SIGINT, app.request_stop)
    signal.signal(signal.SIGTERM, app.request_stop)
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

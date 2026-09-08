#!/usr/bin/env python
"""kabuステーションAPIとの疎通確認のみを行うスクリプト。

kabuステーションを起動した状態のPCで実行する。
トークン取得 → 信用余力取得 → 監視銘柄の板情報取得、まで行い、
実際の発注は一切行わない(sendorder/cancelorderを呼び出すコードはこのファイルに
存在しない)。読み取り専用のため、--env production を指定しても
confirm_live_trading の設定に関わらず実行できる(発注しないので安全)。

使い方:
    python scripts/check_connection.py --config config/config.yaml --env verification
    python scripts/check_connection.py --config config/config.yaml --env production
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "src")

from autotrade.config import ConfigError, load_settings  # noqa: E402
from autotrade.kabu_client import KabuApiError, KabuClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="kabuステーションAPI 疎通確認")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--env", choices=["verification", "production"], default=None)
    args = parser.parse_args()

    try:
        settings = load_settings(args.config, env_override=args.env, require_live_confirmation=False)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 1

    client = KabuClient(base_url=settings.base_url, api_password=settings.api_password)

    print(f"環境: {settings.environment.value} ({settings.base_url})")
    try:
        client.get_token()
        print("✔ トークン取得に成功しました。")
    except KabuApiError as exc:
        print(f"✘ トークン取得に失敗しました: {exc}", file=sys.stderr)
        print("kabuステーションが起動し、ログイン済みであることを確認してください。", file=sys.stderr)
        return 1

    try:
        wallet = client.get_wallet_margin()
        print(f"✔ 信用新規可能額: {wallet.get('MarginAccountWallet')} 円")
    except KabuApiError as exc:
        print(f"✘ 信用余力の取得に失敗しました: {exc}", file=sys.stderr)
        return 1

    # 銘柄登録をしないと板情報の各値がnullで返ってくることがあるため、先に登録する。
    try:
        client.register_symbols(settings.watchlist)
        print(f"✔ 銘柄登録に成功しました({len(settings.watchlist)}銘柄)。")
    except KabuApiError as exc:
        print(f"✘ 銘柄登録に失敗しました: {exc}", file=sys.stderr)

    for spec in settings.watchlist:
        try:
            board = client.get_board(spec.symbol, int(spec.exchange))
            price = board.get("CurrentPrice")
            if price is None:
                print(
                    f"△ {spec.symbol} 現在値: None(登録直後は空のことがあります。"
                    "少し待って再実行するか、取引時間内かどうかを確認してください)"
                )
            else:
                print(f"✔ {spec.symbol} 現在値: {price}")
        except KabuApiError as exc:
            print(f"✘ {spec.symbol} の板情報取得に失敗しました: {exc}", file=sys.stderr)

    print("疎通確認が完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""kabuステーションAPIとの疎通確認のみを行うスクリプト。

kabuステーションを起動した状態のPCで実行する。
トークン取得 → 信用余力取得 → 監視銘柄の板情報取得、まで行い、
実際の発注は一切行わない。

使い方:
    python scripts/check_connection.py --config config/config.yaml --env verification
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
        settings = load_settings(args.config, env_override=args.env)
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

    for spec in settings.watchlist:
        try:
            board = client.get_board(spec.symbol, int(spec.exchange))
            print(f"✔ {spec.symbol} 現在値: {board.get('CurrentPrice')}")
        except KabuApiError as exc:
            print(f"✘ {spec.symbol} の板情報取得に失敗しました: {exc}", file=sys.stderr)

    print("疎通確認が完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

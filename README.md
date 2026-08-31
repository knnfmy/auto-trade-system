# auto-trade-system

三菱UFJ eスマート証券(旧auカブコム証券)の **kabuステーションAPI** を使った、
日本株**信用取引(一般信用・デイトレード)専用**の自動売買システムです。

移動平均クロス(ゴールデンクロス/デッドクロス)を初期戦略として実装しており、
`autotrade/strategy/` 以下に別の戦略を追加すれば差し替えられます。

## ⚠️ 免責事項・重要な注意

- 本システムは投資助言ではなく、あくまで発注を自動化するツールです。**売買の結果生じるいかなる損失についても責任を負いません。**
- 信用取引はレバレッジを伴い、**入金した資金以上の損失(追証)が発生する可能性があります。**
- 必ず**検証環境(ポート18081)で十分に動作確認**してから本番環境に切り替えてください。
- 一般信用(デイトレ)専用として実装しているため、**当日中に建玉を全て返済する強制フラット機能**が入っていますが、
  ネットワーク障害・APIエラー・kabuステーションの異常終了などにより返済注文が失敗する可能性はゼロではありません。
  **運用中は kabuステーションの画面・保有建玉を必ず自分の目でも確認してください。**
- 本システムは kabuステーションAPIの仕様(2026年時点で確認できた範囲)に基づいて実装していますが、
  仕様変更やAPI障害には追従できない場合があります。

## 前提条件

- kabuステーションAPIを利用できる証券口座(信用取引口座を含む)
- Windows PC + kabuステーションアプリ(本APIは `localhost` でのみ待受するため、**このシステムはkabuステーションが起動しているPC上で実行する必要があります**)
- kabuステーションAPIの利用申込・APIパスワード設定済みであること
- Python 3.10以上

## セットアップ

```powershell
git clone <このリポジトリ> auto-trade-system
cd auto-trade-system
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

copy config\config.example.yaml config\config.yaml
copy .env.example .env
# .env を開き、kabuステーションの「システム設定」>「API」タブで設定したAPIパスワードを記入
# config\config.yaml を開き、監視銘柄・戦略パラメータ・リスクパラメータを確認/調整
```

詳細は [docs/SETUP.md](docs/SETUP.md) を参照してください。

## 動作確認・実行手順(必ずこの順番で)

1. **単体テスト**(ネットワーク不要、どの環境でも実行可):
   ```bash
   pytest tests/
   ```
2. **疎通確認**(kabuステーション起動済みのPCで):
   ```powershell
   python scripts\check_connection.py --env verification
   ```
   トークン取得・信用余力取得・監視銘柄の現在値取得ができればOKです。
3. **検証環境 + ドライラン**(実発注なし、ログのみ):
   ```powershell
   python -m autotrade.main --env verification --dry-run
   ```
   ログ(`logs/autotrade.log`)を見て、意図した銘柄・タイミング・数量で発注ログが出ているか確認します。
4. **検証環境での疑似発注**:
   ```powershell
   python -m autotrade.main --env verification
   ```
5. **本番切り替え**: `config/config.yaml` の `environment: production` と `confirm_live_trading: true` を設定してから:
   ```powershell
   python -m autotrade.main --env production
   ```
   (この二重フラグは誤発注防止のための安全装置で、両方揃わないと起動しません。)

## アーキテクチャ

```
src/autotrade/
├── config.py           設定(YAML+.env)ロード、本番切替の安全装置
├── kabu_client.py       kabuステーションAPI REST クライアント(トークン管理・レート制限・リトライ)
├── models.py            Exchange/Side/CashMargin等のEnum、Position等のdataclass
├── bar_builder.py        ティック→分足バー集計(純粋ロジック)
├── market_data.py        WebSocket(PUSH配信)購読 + bar_builderの束ね
├── indicators.py         SMA/EMA・クロス判定
├── strategy/              売買戦略(差し替え可能)
│   ├── base.py
│   └── ma_cross.py        移動平均クロス戦略(デフォルト)
├── risk.py                建玉サイズ計算、日次損失ガード、損切り/利確判定
├── order_manager.py       シグナル→発注、注文/建玉状態の同期、強制フラット
├── trading_calendar.py    東証営業日・立会時間判定
├── state_store.py         売買履歴・日次損益の永続化(SQLite)
└── main.py                 メインループ
```

### 発注の流れ(信用新規→信用返済)

1. `strategy` が確定済みバーからシグナル(`ENTRY_LONG` / `ENTRY_SHORT` / `EXIT`)を判定
2. `risk` が日次損失上限・最大建玉数・建玉サイズをチェック
3. `order_manager` が `POST /sendorder` で信用新規(指値)を発注し、約定を `/orders` のポーリングで検知
4. 約定明細(`Details` の `RecType=8`)から実際の建玉ID(`ExecutionID`)と約定価格を取得し、ローカルで追跡
5. 損切り/利確ライン到達、戦略の逆シグナル、または `force_flatten_time` 到達時に `ClosePositions` で建玉IDを指定して成行返済
6. 返済約定で実現損益を計算し、SQLiteに記録(日次損失ガードに反映)

## 既知の制限事項

- kabuステーションAPIはヒストリカルOHLCを提供しないため、起動後にバーが `warmup_bars` 本貯まるまではシグナルが出ません(起動直後は様子見になります)。
- 約定はPUSH配信されないため、`/orders` を `poll_interval_sec` 間隔でポーリングして検知しています。約定〜検知までにタイムラグがあります。
- 本システムが発注した注文のみを追跡します。kabuステーション画面から手動で発注・返済すると、内部状態と実際の建玉がずれます。
- 祝日判定は `jpholiday` に依存しています(未インストールでも動きますが、祝日を営業日と誤判定しうるため、本番運用前に必ずインストールしてください)。
- バックテスト機能は含まれていません(`strategy` インターフェースは差し替え可能な設計にしてあります)。

## テスト

```bash
pytest tests/ -v
```

HTTP通信はすべてモックしているため、ネットワーク接続なしで実行できます。

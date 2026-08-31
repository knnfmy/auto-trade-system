# セットアップ手順

## 1. 証券口座・信用取引口座の準備

1. 三菱UFJ eスマート証券(旧auカブコム証券)の口座を開設(未開設の場合)。
2. 信用取引口座を開設(審査あり)。本システムは**一般信用(デイトレ)**専用として実装しています。
3. kabuステーションAPIの利用申込を行う(kabuステーション Professional プラン等、API利用に必要なプラン・申込が必要です。最新の条件は公式サイトでご確認ください)。

## 2. kabuステーションのインストール・設定

1. 公式サイトから kabuステーション(Windowsアプリ)をダウンロード・インストールし、ログインします。
2. kabuステーションの「システム設定」→「API」タブを開き、**APIパスワード**を設定します。
   - 本番用・検証用でパスワードを分けて設定できます。両方設定しておくことを推奨します。
3. 同タブで API の使用を有効化します(本番/検証それぞれ)。
4. Windows のファイアウォールが `localhost:18080` / `18081` へのループバック通信をブロックしていないか確認します(通常は追加設定不要です)。

## 3. リポジトリのセットアップ

```powershell
git clone <このリポジトリ> auto-trade-system
cd auto-trade-system
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

copy config\config.example.yaml config\config.yaml
copy .env.example .env
```

`.env` を編集し、kabuステーションで設定したAPIパスワードを入力します。

```
KABU_API_PASSWORD_VERIFICATION=あなたの検証用APIパスワード
KABU_API_PASSWORD_PRODUCTION=あなたの本番用APIパスワード
```

`.env` は `.gitignore` されているためコミットされません。**絶対にリポジトリにコミットしないでください。**

## 4. config/config.yaml の調整

- `watchlist`: 監視する銘柄コード・市場コード(最大50銘柄)。
- `account.account_type`: 口座区分(2=一般 / 4=特定 / 12=法人)。証券口座の設定に合わせてください。
- `strategy`: 移動平均の期間・分足の周期。
- `risk`: 1トレードあたりの資金配分、最大同時建玉数、損切り/利確ライン、日次最大損失額。
  **実運用前に必ず自分のリスク許容度に合わせて調整してください。デフォルト値は一例に過ぎません。**
- `schedule.force_flatten_time`: 引け前の強制返済時刻。デイトレのため15:00より前(デフォルト14:55)に設定します。

## 5. 動作確認

kabuステーションを起動し、ログインした状態で:

```powershell
python scripts\check_connection.py --env verification
```

トークン取得・信用余力・監視銘柄の現在値が正常に取得できることを確認します。
失敗する場合は、kabuステーションが起動・ログイン済みか、API設定が有効になっているか、
`.env` のパスワードが正しいかを確認してください。

## 6. 検証環境での試運転

```powershell
python -m autotrade.main --env verification --dry-run
```

`logs/autotrade.log` を確認し、意図した通りに戦略が判定・発注(ログ上のみ)されているか確認します。
問題なければ `--dry-run` を外し、検証環境上での実際の(疑似)発注を確認してください。

## 7. 本番環境への切り替え

`config/config.yaml` で以下の2つを設定します(誤発注防止のための二重確認です):

```yaml
environment: production
confirm_live_trading: true
```

その後:

```powershell
python -m autotrade.main --env production
```

**本番環境では実際の資金で発注されます。** 少額・少ない監視銘柄数から始めることを強く推奨します。

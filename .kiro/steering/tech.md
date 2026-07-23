# 技術スタック

## アーキテクチャ

Docker Compose をローカル開発の標準実行環境とし、Frontend、Backend、MySQL を独立サービスとして構成します。

```text
Browser ---------------------> Vite (/api proxy) -> Django REST API -> MySQL
LINE / Smartphone -> ngrok --/                        |
                         LIFF / LINE Login ------------+
                                                      +-> LINE Messaging API
```

ブラウザは相対パス `/api/...` で Backend と通信します。データベースと外部 API の認証情報には Backend だけがアクセスします。

ngrokは通常のDocker Composeサービスとして他のサービスと一緒に起動します。単一のHTTPSトンネルをFrontendへ接続し、`/api`は既存のVite proxyを経由させます。

認証付きの owner 操作と LINE Webhook は同じ Django API に到達しますが、信頼境界は分けます。owner 操作は Backend が検証した LINE identity、サーバー側 session、exact-origin CSRF で保護し、公開 Webhook はチャネル別 URL と署名検証によって認証します。

## コア技術

- **Frontend**: TypeScript 6、React 19、Vite 8
- **Backend**: Python 3.14、Django 6、Django REST Framework 3
- **Database**: MySQL 8.4、文字セット `utf8mb4`
- **Runtime**: Docker、Docker Compose
- **LINE integration**: LIFF SDK、LINE Bot SDK、HTTPX
- **Credential encryption**: `cryptography` の Fernet／MultiFernet

Frontend は ES Modules、React JSX transform、ES2022 を前提とします。Backend は日本語、Asia/Tokyo、timezone-aware datetime を既定とします。

## 依存関係の管理

- Frontend は `package-lock.json` と `npm ci` で再現可能なインストールを行う
- Backend は `requirements.txt` で依存バージョンを固定する
- 開発者個人のホスト環境差より、コンテナ内のランタイムを優先する

全依存をステアリングへ転記せず、開発パターンを左右する主要技術だけを記録します。

## 開発標準

### 型安全性

- TypeScript は `strict`、`isolatedModules`、`noEmit` を有効にする
- JavaScript を混在させず、API レスポンス等の境界データには型を与える
- Production build は `tsc -b` を Vite build より先に実行し、型エラーをビルドの失敗とする

### API

- HTTP API は `/api/` 配下に置く
- Django REST Framework の View と Response を使い、公開契約を HTTP テストで検証する
- 外部サービス呼び出しは Backend に閉じ込め、Frontend から LINE API を直接呼ばない
- owner 向け API はサーバー側 session で本人状態を確認し、状態変更では exact origin と CSRF token の両方を検証する
- 公開 Webhook は owner session の対象外とし、署名検証前の body や識別情報を信頼しない

### 秘密情報と環境設定

- 環境差分と秘密値は環境変数で注入する
- `.env.example` には必要なキー名と安全なローカル例だけを置き、実際の `.env` はコミットしない
- LINE のトークン、シークレット、ユーザー ID は Backend サービスだけへ渡す
- Messaging API チャネルのアクセストークンとシークレットは認証付き暗号で DB へ保存し、専用 keyring だけを Backend の環境変数へ渡す
- LINE Login の secret と owner allowlist 用 digest は Backend に閉じ込め、LIFF ID だけを公開設定として Frontend へ渡す
- ngrok の authtoken は開発インフラ用の秘密情報として `.env` から ngrok サービスだけへ渡す
- リポジトリ内の既定パスワードや secret はローカル開発専用とし、本番相当環境では必ず上書きする
- 秘密情報を含む DB の general query log は無効にし、ログや例外は秘密値を保持しない安全な分類へ変換する

### テスト

- Frontend は Vitest と jsdom を使い、テストコードを `/frontend/test/` に置く
- Backend は Django test runner と DRF `APITestCase` を使い、status code と response body の両方を検証する
- Frontend・Backendとも、各テスト定義の直前に日本語コメントで `テストケース:` と `期待値:` を1行ずつ記載し、入力・操作と観測可能な期待結果を具体的に示す
- 外部作用、状態 projection、並行更新を扱う境界は、単体・統合に加えて競合、安全性、処理時間と query budget をリスクに応じて検証する
- 現時点で CI、Python 静的型検査、coverage、E2E、共通 lint/formatter は導入されていないため、未確立の必須基準を仮定しない

## 共通コマンド

```bash
# 起動
docker compose up --build

# Frontend テスト
docker compose run --rm frontend npm test

# Backend テスト
docker compose run --rm backend python manage.py test --settings=config.test_settings

# Frontend production build
docker compose run --rm frontend npm run build

# ログ確認 / 停止
docker compose logs -f
docker compose down
```

`docker compose down -v` はデータベース volume も削除する破壊的操作として区別します。

ngrokの割り当て済み開発用ドメインを`NGROK_DOMAIN`、authtokenを`NGROK_AUTHTOKEN`として`.env`へ設定します。Viteはそのドメインだけを追加Hostとして許可し、任意Hostを許可しません。ngrokの検査APIはホストの`127.0.0.1:4040`にだけ公開します。Compose起動中は公開トンネルも有効になるため、公開URLを共有せず、利用後は全サービスを停止します。

## 重要な技術判断

### 起動順序と健全性

Backend は MySQL の healthcheck 成功後に起動し、起動時に migration を適用します。Frontend は Backend コンテナの起動後に開始します。定期的な Backend API の healthcheck は実行しません。

### LINE 配信

送信処理は Backend のサービス境界に閉じ込めます。プレビュー時に正規化済み内容を確認トークンへ結び付け、送信時に内容の一致を再検証します。トークンは不透明な値とし、本文や操作 ID を含めません。

操作 ID を LINE retry key と監査レコードに一貫して使用し、同じ操作は保存済み結果へ収束させます。外部通信はデータベース transaction の外で行い、処理中レコードの一意制約と条件付き更新で並行送信や結果の上書きを防ぎます。

配信状態は `processing`、`succeeded`、`failed`、`unknown` を区別します。タイムアウト等の結果不明時は自動再送せず、状態確認 API で既存操作を確認してから明示的な再試行を許可します。LINE SDK の生の例外や認証情報、固定宛先は公開 API や通常ログへ出さず、安全なエラー分類へ変換します。

現在の push 送信が参照する秘密値はアクセストークンと固定ユーザー ID です。チャネルシークレットは push 送信では使用せず、Webhook 境界だけが署名検証のために参照します。利用上限確認は将来の運用機能として扱います。

### LINE アカウントとチャネル資格情報

LIFF から得た token は Backend の LINE Login 境界で検証し、provider と owner allowlist に一致した identity だけをサーバー側 session へ結び付けます。Frontend は session cookie を直接解釈せず、session API の安全な状態表現を使います。

複数 Messaging API チャネルの資格情報は DB へ暗号化して保存し、復号可能な値を repository 境界の外へ不必要に広げません。keyring の先頭を現用鍵とし、旧鍵を残した再暗号化、検証、撤去の順でローテーションします。鍵を失った DB は復号できないため、バックアップと旧鍵の保持期間を一体で判断します。

### Webhook

チャネル別の不透明な UUID から有効な資格情報を選び、生の request body に対する HMAC-SHA256 署名検証を JSON 解析より先に行います。署名後も `destination` と payload 上限を検証し、検証前後の失敗を安全な公開エラーへ縮約します。

`webhookEventId` はイベント台帳の一意キーとして重複を排除し、検証済みの immutable envelope だけを静的 handler registry へ渡します。受付は軽量な同期処理とし、未対応イベントも台帳へ明示的に記録します。将来重い処理が必要になった場合はレスポンス返却から分離します。

follow／unfollow handler は、active owner、provider、LINE subject、チャネルが完全一致する既存配信先だけを状態 projection の対象にします。未連携、不正、group／room source から identity や配信先を作成せず、安全な非更新結果として監査します。

友だち状態、最終イベントの順序 cursor、PII を含まない同期監査は、行ロックを使った同一 transaction で確定します。登録時刻を baseline とし、`(occurred_at_ms, webhookEventId の ASCII 順)` を比較して、遅延、重複、同時刻、同状態のイベントを到着順に依存しない単一状態へ収束させます。message／postback、reply、配信は別 handler の責任です。

message／postback handler は、完全一致の静的 command／action registry と既存の owner・provider・recipient 照合を通過した入力だけを処理します。現在の command は `/ping` から固定 `pong` 一件への reply に限定し、production の postback action registry は明示登録がない限り空です。未知、不正、未連携、group／room source は identity や recipient を作らず、外部作用のない結果として扱います。

Webhook request は View 入口から単一の monotonic deadline を共有し、handler を local と deadline-managed external の実行プロファイルへ分けます。LINE reply は同一チャネルの資格情報と一回限りの reply token を使い、自動再試行せず、期限不足なら開始しません。accepted、rejected、unknown を区別し、受信内容、token、LINE user ID、access token を保存しない interaction 監査へ収束させます。

---
_更新日: 2026-07-23。許可リスト型 interaction、deadline-aware reply、PII-free 監査の実装パターンを反映。技術判断と標準を記録し、依存パッケージ一覧にはしない。_

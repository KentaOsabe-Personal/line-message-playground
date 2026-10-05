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
- **Routing**: React Router 8 の Declarative mode
- **Styling**: Tailwind CSS 4 と `@tailwindcss/vite`
- **Backend**: Python 3.14、Django 6、Django REST Framework 3
- **Database**: MySQL 8.4、文字セット `utf8mb4`
- **Runtime**: Docker、Docker Compose
- **LINE integration**: LIFF SDK、LINE Bot SDK、HTTPX
- **Credential encryption**: `cryptography` の Fernet／MultiFernet
- **Deterministic image generation**: Pillow と版・digest を固定した同梱日本語フォント

Frontend は ES Modules、React JSX transform、ES2022 を前提とします。Backend は日本語、Asia/Tokyo、timezone-aware datetime を既定とします。

Frontendの配色は共通のテーマトークンで管理します。既定の「自動」では`prefers-color-scheme`でブラウザが通知する配色に追従し、「ライト」「ダーク」を選んだ場合は手動設定を優先します。選択はブラウザのlocalStorageに保存し、保存できない場合も画面を開いている間は反映します。暗い背景で状態を示す文字色と、白い文字を表示する操作用の背景色には別のトークンを使い、両配色でコントラストを保ちます。

## 依存関係の管理

- Frontend は `package-lock.json` と `npm ci` で再現可能なインストールを行う
- Backend は `requirements.txt` で依存バージョンを固定する
- 開発者個人のホスト環境差より、コンテナ内のランタイムを優先する

全依存をステアリングへ転記せず、開発パターンを左右する主要技術だけを記録します。

## 開発標準

サービス固有の規則は [Frontend AGENTS](../../frontend/AGENTS.md) と [Backend AGENTS](../../backend/AGENTS.md) を参照します。設計時・root起点の作業でも対象サービスの規則を読みます。

### 秘密情報と環境設定

- 環境差分と秘密値は環境変数で注入する
- `.env.example` には必要なキー名と安全なローカル例だけを置き、実際の `.env` はコミットしない
- LINE のトークン、シークレット、ユーザー ID は Backend サービスだけへ渡す
- Messaging API チャネルのアクセストークンとシークレットは認証付き暗号で DB へ保存し、専用 keyring だけを Backend の環境変数へ渡す
- LINE Login の secret と owner allowlist 用 digest は Backend に閉じ込め、LIFF ID だけを公開設定として Frontend へ渡す
- ngrok の authtoken は開発インフラ用の秘密情報として `.env` から ngrok サービスだけへ渡す
- リポジトリ内の既定パスワードや secret はローカル開発専用とし、本番相当環境では必ず上書きする
- 秘密情報を含む DB の general query log は無効にし、ログや例外は秘密値を保持しない安全な分類へ変換する

### テストと検証の範囲

サービスごとのテスト配置・日本語コメント規約は各AGENTSにあります。BackendはRuffのLint・整形、FrontendはESLint／typescript-eslintの型情報を使うLint、React Hooks検査、Prettierの整形、TypeScriptの型検査をローカルで実行します。正本はREADMEの「ローカル品質チェック」と `sh scripts/check.sh [all|frontend|backend]` です。実装・受入・PR公開前に変更範囲に対応するサービス全体を検査し、失敗・未実施を合格扱いしません。GitHub Actionsは使用しません。Python静的型検査、coverage、E2Eの必須基準は未導入です。

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

### LINE連携

送信、認証・資格情報、Webhook、リッチメニューの詳細は [line-integration.md](line-integration.md) にあります。これらの機能・画面・契約の設計や変更時に読みます。結果不明を成功扱いせず、外部作用の自動再送を避け、所有権を証明できない外部資源を変更・削除しない原則を守ります。

_移行日: 2026-09-27。サービス固有規則とLINE契約を局所・条件付き参照へ移動。_

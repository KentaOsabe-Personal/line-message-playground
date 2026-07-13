# 技術スタック

## アーキテクチャ

Docker Compose をローカル開発の標準実行環境とし、Frontend、Backend、MySQL を独立サービスとして構成します。

```text
Browser -> Vite (/api proxy) -> Django REST API -> MySQL
                                  |
                                  +-> LINE Messaging API
```

ブラウザは相対パス `/api/...` で Backend と通信します。データベースと外部 API の認証情報には Backend だけがアクセスします。

## コア技術

- **Frontend**: TypeScript 6、React 19、Vite 8
- **Backend**: Python 3.14、Django 6、Django REST Framework 3
- **Database**: MySQL 8.4、文字セット `utf8mb4`
- **Runtime**: Docker、Docker Compose

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

### 秘密情報と環境設定

- 環境差分と秘密値は環境変数で注入する
- `.env.example` には必要なキー名と安全なローカル例だけを置き、実際の `.env` はコミットしない
- LINE のトークン、シークレット、ユーザー ID は Backend サービスだけへ渡す
- リポジトリ内の既定パスワードや secret はローカル開発専用とし、本番相当環境では必ず上書きする

### テスト

- Frontend は Vitest と jsdom を使い、テストコードを `/frontend/test/` に置く
- Backend は Django test runner と DRF `APITestCase` を使い、status code と response body の両方を検証する
- Frontend・Backendとも、各テスト定義の直前に日本語コメントで `テストケース:` と `期待値:` を1行ずつ記載し、入力・操作と観測可能な期待結果を具体的に示す
- 現時点で coverage、E2E、共通 lint/formatter は導入されていないため、未確立の必須基準を仮定しない

## 共通コマンド

```bash
# 起動
docker compose up --build

# Frontend テスト
docker compose run --rm frontend npm test

# Backend テスト
docker compose run --rm backend python manage.py test

# Frontend production build
docker compose run --rm frontend npm run build

# ログ確認 / 停止
docker compose logs -f
docker compose down
```

`docker compose down -v` はデータベース volume も削除する破壊的操作として区別します。

## 重要な技術判断

### 起動順序と健全性

Backend は MySQL の healthcheck 成功後に起動し、起動時に migration を適用します。Frontend は Backend コンテナの起動後に開始します。定期的な Backend API の healthcheck は実行しません。

### LINE 配信

送信処理は Backend のサービス境界に閉じ込めます。プレビュー時に正規化済み内容を確認トークンへ結び付け、送信時に内容の一致を再検証します。トークンは不透明な値とし、本文や操作 ID を含めません。

操作 ID を LINE retry key と監査レコードに一貫して使用し、同じ操作は保存済み結果へ収束させます。外部通信はデータベース transaction の外で行い、処理中レコードの一意制約と条件付き更新で並行送信や結果の上書きを防ぎます。

配信状態は `processing`、`succeeded`、`failed`、`unknown` を区別します。タイムアウト等の結果不明時は自動再送せず、状態確認 API で既存操作を確認してから明示的な再試行を許可します。LINE SDK の生の例外や認証情報、固定宛先は公開 API や通常ログへ出さず、安全なエラー分類へ変換します。

現在の push 送信が参照する秘密値はアクセストークンと固定ユーザー ID です。チャネルシークレットは Webhook の署名検証を導入するまで送信処理で使用しません。利用上限確認は将来の運用機能として扱います。

### Webhook

導入時は生の request body に対する HMAC-SHA256 署名検証を JSON 解析より先に行います。`webhookEventId` で重複を排除し、重い処理はレスポンス返却から分離します。

---
_更新日: 2026-07-12。技術判断と標準を記録し、依存パッケージ一覧にはしない。_

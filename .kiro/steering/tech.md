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

- Frontend は Vitest と jsdom を使い、`*.test.tsx` を対象実装の近くに置く
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

サービス間依存は単なるプロセス起動ではなく healthcheck で判定します。Backend は起動時に migration を適用し、Frontend は健全な Backend を待ちます。

### LINE 配信

送信処理は Backend のサービス境界に閉じ込めます。冪等性キー、送信監査、LINE リクエスト ID、利用上限確認を設計上の主要関心事とします。

### Webhook

導入時は生の request body に対する HMAC-SHA256 署名検証を JSON 解析より先に行います。`webhookEventId` で重複を排除し、重い処理はレスポンス返却から分離します。

---
_更新日: 2026-07-11。技術判断と標準を記録し、依存パッケージ一覧にはしない。_

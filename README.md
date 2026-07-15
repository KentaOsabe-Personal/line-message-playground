# LINE Message Playground

LINE配信機能を検証するための開発環境です。

## 技術スタック

- Frontend: TypeScript 6.0.3 / React 19.2.7 / Vite 8 / Vitest 4
- Backend: Python 3.14 / Django 6.0.7 / Django REST Framework 3.17.1
- Database: MySQL 8.4
- Runtime: Docker / Docker Compose

## セットアップ

Docker と Docker Compose が利用できることを確認し、リポジトリ直下で実行します。

```bash
cp .env.example .env
```

ngrokダッシュボードでauthtokenと割り当て済みの開発用ドメインを確認し、`.env`へ設定します。`NGROK_DOMAIN`にはスキームを付けず、ホスト名だけを指定します。

```dotenv
NGROK_AUTHTOKEN=your-ngrok-authtoken
NGROK_DOMAIN=your-domain.ngrok-free.app
```

設定後、全サービスを起動します。ngrokも通常のComposeサービスとして起動します。

```bash
docker compose up --build
```

起動後、以下へアクセスできます。

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000/api/health/
- Django Admin: http://localhost:8000/admin/

バックエンドは起動時にマイグレーションを自動適用します。

## スマートフォンからの確認

ngrokの開発用ドメインを使うと、スマートフォンのLINEアプリからローカルのFrontendと`/api`へHTTPSでアクセスできます。

起動後は、設定した開発用ドメイン（例: `https://your-domain.ngrok-free.app`）でFrontendを確認できます。`/api`はViteの既存proxyを経由してBackendへ転送されます。ngrokのローカル検査画面は http://127.0.0.1:4040 です。

現時点の配信APIはローカル利用を前提として認証がないため、公開URLを共有せず、利用後は`docker compose down`で全サービスとトンネルを停止します。ngrokのauthtokenはLINEのチャネル資格情報とは別の秘密情報として`.env`だけで管理します。

## テスト

```bash
docker compose run --rm frontend npm test
docker compose run --rm backend python manage.py test
```

## よく使うコマンド

```bash
# バックグラウンド起動（ngrokを含む）
docker compose up --build -d

# ログ確認
docker compose logs -f

# 停止
docker compose down

# DBデータも削除
docker compose down -v
```

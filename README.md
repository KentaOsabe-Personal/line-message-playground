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

### チャネル資格情報の暗号化キー

Backend の起動前に、チャネル資格情報専用の Fernet 鍵を一度だけ生成します。次のコマンドは鍵を引数やシェル履歴へ含めず、標準出力へ生成結果だけを表示します。

```bash
docker compose run --rm --no-deps backend python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode('ascii'))"
```

表示された値をローカルの `.env` にある空の `LINE_CHANNEL_CREDENTIAL_KEYS` へ設定してください。値は canonical URL-safe Base64 で表現された32 byteの Fernet 鍵でなければなりません。複数鍵を使うローテーション期間は、現用鍵を先頭、読取専用の旧鍵を後続にしてカンマだけで連結します。空要素、空白、quote、改行、重複鍵は受け付けません。鍵をREADME、`.env.example`、Git、チャット、ログへ保存しないでください。

Backend は `DJANGO_DEBUG=false` と有効な専用 keyring を起動条件とし、MySQL は general query log を無効にします。条件を満たさない場合は、マイグレーションやDB接続より前に安全に停止します。

```bash
docker compose up --build
```

起動後、以下へアクセスできます。

- Frontend: http://localhost:5173
- Backend API: http://localhost:8000/api/health/
- Django Admin: http://localhost:8000/admin/

バックエンドは起動時にマイグレーションを自動適用します。

チャネル管理コマンドで現在のアクセストークンとチャネルシークレットを初期登録し、登録済み資格情報を正常に利用できることを確認した後、従来の `LINE_CHANNEL_SECRET` がローカル `.env` に残っていれば削除してください。既存配信が利用する `LINE_CHANNEL_ACCESS_TOKEN` と `LINE_USER_ID` は、配信機能の移行が完了するまで維持します。

### 暗号化キーのローテーション

1. 新しい鍵を一度だけ生成し、`LINE_CHANNEL_CREDENTIAL_KEYS` の先頭へ追加します。旧鍵は後続に残します。
2. 全Backendプロセスを再起動し、ローテーションコマンドを完了するまで再実行します。中断時も旧鍵を削除しません。
3. 全資格情報が現用鍵で検証済みとなり、旧鍵撤去可能の結果が出たことを確認します。
4. DB backup と、そのbackupの復元に必要な旧鍵の保管期間を確認します。backupを読める必要がある間は、旧鍵をDBとは別の安全な保管先で維持します。
5. 復元要件を満たした後だけ旧鍵を環境から撤去し、全Backendプロセスを再起動します。

ローテーション中の keyring とbackupを同時に失うと保存済み資格情報を復号できません。ローテーション完了前、または旧backupを復元する可能性がある間は旧鍵を破棄しないでください。

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

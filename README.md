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

### LINE Login / LIFF runtime

Backend の署名 secret は既知値や短い値を使用せず、ローカル環境ごとに生成します。次のコマンドは Django を起動せず、生成値だけを標準出力へ表示します。

```bash
docker compose run --rm --no-deps backend python -c "import secrets; print(secrets.token_urlsafe(48))"
```

生成値と LINE Developers Console の設定を `.env` へ保存します。`LINE_LOGIN_CHANNEL_SECRET` と `LINE_OWNER_SUBJECT_DIGEST` は Backend だけへ渡され、Frontend の環境や bundle には含まれません。

```dotenv
DJANGO_SECRET_KEY=<生成した値>
VITE_LIFF_ID=<LIFF ID>
LINE_LOGIN_CHANNEL_ID=<LINE Login channel ID>
LINE_LOGIN_CHANNEL_SECRET=<LINE Login channel secret>
LINE_LOGIN_PROVIDER_ID=<provider ID>
LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID=<登録済みMessaging API channelのpublic UUID>
LINE_OWNER_SUBJECT_DIGEST=
```

LIFF は `VITE_LIFF_ID` から `https://liff.line.me/${VITE_LIFF_ID}` を導出します。LINE Developers Console の LIFF Endpoint URL は `https://${NGROK_DOMAIN}/liff`、scope は `openid profile` に設定し、LIFF と LINE Login と Messaging API channel が同じ provider に属することを確認してください。`NGROK_DOMAIN` を変更した場合は Console の Endpoint URL も同時に更新します。scheme、port、path、wildcard、空白を含む `NGROK_DOMAIN` は起動時に拒否されます。

他の必須値を設定し `LINE_OWNER_SUBJECT_DIGEST` だけを空にした状態では、Backend は起動できますが owner 認証は fail closed になります。次のコマンドで本人識別情報を非表示入力し、表示された lowercase SHA-256 digest だけを `.env` の `LINE_OWNER_SUBJECT_DIGEST` へ設定して Backend を再起動します。本人識別情報を引数、README、ログへ保存しないでください。

```bash
docker compose run --rm backend python manage.py derive_line_owner_digest
```

既存の Backend 専用 `LINE_USER_ID` を入力源にする場合だけ、`--use-line-user-id` を指定できます。このコマンドも本人識別情報自体は出力しません。

```bash
docker compose run --rm backend python manage.py derive_line_owner_digest --use-line-user-id
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

新しいチャネルの登録時は、LINE Developers Consoleで確認したprovider IDを入力します。provider IDは1〜64文字のASCII数字列としてそのまま保存され、空白除去・整数化・leading zero除去は行いません。既存チャネルはmigration後もprovider未設定のまま利用できますが、アカウント連携候補には表示されません。既存チャネルの公開UUIDを指定して、次の非対話コマンドで安全にbackfillします。

```bash
docker compose run --rm backend python manage.py manage_line_channel \
  --channel-public-id <既存チャネルの公開UUID> \
  --provider-id <LINE provider ID>
```

出力の `provider_id` が設定値と完全一致することを確認してください。出力にはチャネル資格情報は含まれません。`LINE_LIFF_LINKED_CHANNEL_PUBLIC_ID` が指すチャネルには、`LINE_LOGIN_PROVIDER_ID` と完全一致するprovider IDを設定する必要があります。

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
docker compose run --rm backend python manage.py test --settings=config.test_settings
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

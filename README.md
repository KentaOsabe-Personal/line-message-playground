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

既存の Backend 専用 `LINE_USER_ID` を本人digest生成の入力源にする場合だけ、`--use-line-user-id` を指定できます。現在の配信は登録済みの配信先を使うため、この環境変数の設定は配信に不要です。通常は上記の非表示入力を使います。どちらの方法でも本人識別情報自体は出力しません。

```bash
docker compose run --rm backend python manage.py derive_line_owner_digest --use-line-user-id
```

### 文章判定ラボ / LINE Mini App runtime

現在、文章判定ラボと管理画面は同じ開発用LINE Mini App channelを使います。管理画面用の`VITE_LIFF_ID`と文章判定ラボ用の`VITE_TEXT_JUDGMENT_LAB_LIFF_ID`には同じLIFF IDを設定し、`LINE_LOGIN_CHANNEL_ID`と`TEXT_JUDGMENT_LAB_CHANNEL_ID`にも同じMini App channel IDを設定します。`LINE_LOGIN_CHANNEL_SECRET`には同じMini AppのDeveloping channel secretを設定します。

LINE Developers ConsoleのEndpoint URLは`https://${NGROK_DOMAIN}/liff`に設定します。管理画面はLIFF URLの基底`https://liff.line.me/${VITE_LIFF_ID}`、文章判定ラボは追加path付きの`https://liff.line.me/${VITE_TEXT_JUDGMENT_LAB_LIFF_ID}/labs/text-judgment`から開きます。直接確認する場合のラボURLは`https://${NGROK_DOMAIN}/liff/labs/text-judgment`です。`/labs/text-judgment`は互換入口としてcanonicalなラボURLへ転送されます。

Mini App channelのWeb app settingsでは`openid`と`profile`を有効にし、対象のLINE公式アカウントを紐づけます。管理画面は起動時に`profile`の同意状態を確認し、未同意の場合だけMini Appの検証画面を表示します。権限が得られない場合は、プロフィールを含まないID tokenをBackendへ送らず認証を停止します。

LINE Developers Consoleで開発用Mini App channelを開き、次の値を確認します。

| 環境変数 | 取得元・設定内容 | 公開範囲 |
| --- | --- | --- |
| `VITE_TEXT_JUDGMENT_LAB_LIFF_ID` | Mini AppのLIFF ID。例: `1234567890-AbCdEf` | Frontendへ公開 |
| `TEXT_JUDGMENT_LAB_CHANNEL_ID` | Mini Appのchannel ID。ID token検証時の`client_id` / `audience` | Backendのみ |
| `TEXT_JUDGMENT_LAB_OWNER_DIGEST` | 下記手順でchannel IDと本人のprovider単位user IDから導出 | Backendのみ |
| `TEXT_JUDGMENT_LAB_ORIGIN` | `https://${NGROK_DOMAIN}`。末尾slash、path、portなし | Backendのみ |
| `TYPESAFE_API_KEY` | TypeSafe/Jevから発行されたAPI key | Backend秘密情報 |
| `TEXT_JUDGMENT_LAB_MODEL` | 固定値`jev-1.13.0` | Backendのみ |
| `TEXT_JUDGMENT_LAB_ENABLED` | 既定は`false`。必要な設定を揃え、ラボを利用するときだけ`true` | Backendのみ |

#### 本人digestの生成

Mini App、既存LINE Login、Messaging API channelが同じproviderに属する場合、同じ本人にはチャネル種別を問わず同じLINE user IDが割り当てられます。そのため、digest生成だけを目的にMini App ID tokenを手動取得する必要はありません。詳細はLINE公式の[provider設計の説明](https://developers.line.biz/en/tips/2026/06/25/provider-design-basics/)を参照してください。

1. LINE Developers Consoleで、同じproviderに属する既存LINE Login channelまたはMessaging API channelを開きます。
2. ［チャネル基本設定］の［あなたのユーザーID］をコピーします。値は`U`と32桁の小文字16進数です。
3. Mini App channelのchannel IDと、コピーしたユーザーIDを次のコマンドへ入力します。

```zsh
derive_text_judgment_lab_owner_digest() {
  local lab_channel_id line_user_id owner_digest
  read -r "lab_channel_id?Mini App Channel ID: "
  read -rs "line_user_id?同一providerの「あなたのユーザーID」: "
  printf '\n'

  if ! printf '%s' "${lab_channel_id}" | grep -Eq '^[0-9]{1,64}$'; then
    printf 'Mini App Channel IDの形式が不正です。\n' >&2
    return 1
  fi

  if ! printf '%s' "${line_user_id}" | grep -Eq '^U[0-9a-f]{32}$'; then
    printf 'LINE user IDの形式が不正です。\n' >&2
    return 1
  fi

  owner_digest=$(
    printf '%s:%s' "${lab_channel_id}" "${line_user_id}" |
    shasum -a 256 |
    awk '{print $1}'
  )

  printf 'TEXT_JUDGMENT_LAB_CHANNEL_ID=%s\n' "${lab_channel_id}"
  printf 'TEXT_JUDGMENT_LAB_OWNER_DIGEST=%s\n' "${owner_digest}"
}

derive_text_judgment_lab_owner_digest
unfunction derive_text_judgment_lab_owner_digest
```

表示された2行を`.env`へ設定します。`TEXT_JUDGMENT_LAB_OWNER_DIGEST`は既存の`LINE_OWNER_SUBJECT_DIGEST`と導出式が異なるため、既存digestそのものは流用しません。

#### Mini App ID tokenとは

Mini App ID tokenは、LINEが現在のログイン利用者へ発行する有効期間の短いJWTです。`.env`へ保存する設定値ではありません。Mini AppのLIFF設定では`openid` scopeを有効にします。ID tokenの取得条件と有効期間はLINE公式の[`liff.getIDToken()`リファレンス](https://developers.line.biz/en/reference/liff/#get-id-token)を参照してください。

通常フローでは、Frontendがページ起動時に次の処理を行います。

```typescript
import liff from '@line/liff'

await liff.init({ liffId: import.meta.env.VITE_TEXT_JUDGMENT_LAB_LIFF_ID })
const idToken = liff.getIDToken()
```

Frontendは取得した生ID tokenをラボAPIの`Authorization: Bearer ...`へ設定し、画面、URL、storage、通常ログには保存しません。BackendはtokenをLINEの`POST https://api.line.me/oauth2/v2.1/verify`へ一度だけ送り、`client_id`として`TEXT_JUDGMENT_LAB_CHANNEL_ID`を指定します。LINEが返した`iss`、`aud`、`exp`、`sub`を検証し、`SHA-256("<aud>:<sub>")`が設定済みowner digestと一致する場合だけ本人として許可します。

通常、利用者がID tokenを求めたりコピーしたりする操作はありません。Frontendの認証実装が`liff.getIDToken()`を呼び、そのままBackendへ送ります。

ID token検証を手動確認する必要がある場合だけ、ブラウザ開発者ツールのSourcesで`const idToken = liff.getIDToken()`の直後にbreakpointを置き、ローカル変数`idToken`を一時的にコピーして次を実行します。iPhone上のMini Appを調べる場合はmacOS SafariのWebインスペクタから対象ページへ接続します。tokenをコマンドラインへ直接書かず、非表示入力します。確認後はtokenと応答を保存せず破棄します。

```zsh
verify_text_judgment_lab_id_token() {
  local lab_channel_id lab_id_token verify_response
  read -r "lab_channel_id?Mini App Channel ID: "
  read -rs "lab_id_token?Mini App ID token: "
  printf '\n'

  verify_response=$(
    curl --fail --silent --show-error \
      -X POST 'https://api.line.me/oauth2/v2.1/verify' \
      --data-urlencode "id_token=${lab_id_token}" \
      --data-urlencode "client_id=${lab_channel_id}"
  ) || {
    printf 'ID tokenの検証に失敗しました。\n' >&2
    return 1
  }

  printf '%s' "${verify_response}" | jq '{iss, aud, exp, sub}'
}

verify_text_judgment_lab_id_token
unfunction verify_text_judgment_lab_id_token
```

出力の`aud`はMini App channel ID、`sub`は同一providerの［あなたのユーザーID］と一致する必要があります。この手動確認はトラブルシュート用であり、通常起動のたびに行う操作ではありません。

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

### Messaging APIチャネルの登録

チャネル管理画面には、同じMessaging APIチャネルのチャネルID、Bot user ID、チャネルアクセストークン、チャネルシークレット、provider IDを登録します。

Bot user IDは、LINE Developers Consoleの［チャネル基本設定］に表示される［あなたのユーザーID］ではありません。［あなたのユーザーID］は開発者本人のLINEユーザーIDです。また、`@`で始まるベーシックIDや数字のチャネルIDとも異なります。ここで必要なのは、チャネルアクセストークンを使って[LINE公式アカウント（ボット）の情報を取得するAPI](https://developers.line.biz/ja/reference/messaging-api/#get-bot-info)が返す`userId`です。

チャネルアクセストークンをコマンドラインへ直接記述せず、非表示で入力して確認します。次のコマンドが返すJSONの`userId`（`U`と32桁の小文字16進数）を、管理画面の［bot user ID］へ設定してください。

```bash
read -s "LINE_BOT_TOKEN?チャネルアクセストークン: " && echo
curl -sS -H "Authorization: Bearer ${LINE_BOT_TOKEN}" https://api.line.me/v2/bot/info
unset LINE_BOT_TOKEN
```

各IDの違いは次のとおりです。

| 値 | 形式 | 登録先・用途 |
| --- | --- | --- |
| Messaging APIチャネルID | ASCII数字列 | 管理画面の［Messaging API チャネル ID］ |
| Bot user ID | `U` + 32桁の小文字16進数 | `/v2/bot/info`の`userId`を管理画面の［bot user ID］へ登録 |
| あなたのユーザーID | `U` + 32桁の小文字16進数 | 開発者本人のID。Bot user IDには使用しない |
| ベーシックID | `@`から始まる文字列 | LINE公式アカウントの検索・表示用。Bot user IDには使用しない |
| provider ID | ASCII数字列 | 管理画面の［provider ID］ |

登録後はチャネルを有効化し、［接続を確認］を実行します。［接続できました］と表示されたら、表示されたWebhook URLをLINE Developers Consoleの［Messaging API設定］へ設定し、［検証］を実行してください。接続確認はアクセストークンとBot user IDの組み合わせだけを確認するため、チャネルシークレットとWebhookはLINE Developers Console側の検証成功まで確認できません。

現在の配信は、選択した登録済みチャネルの暗号化資格情報と、本人連携済みの配信先を使います。固定設定の `LINE_CHANNEL_ACCESS_TOKEN` と `LINE_USER_ID` は配信に使用せず、選択した資格情報が利用できない場合も固定設定へ切り替えません。

登録済み資格情報を正常に利用できることを確認した後、旧設定の `LINE_CHANNEL_SECRET` と `LINE_CHANNEL_ACCESS_TOKEN` がローカル `.env` に残っていれば削除してください。`LINE_USER_ID` も、上記の本人digest生成コマンドで `--use-line-user-id` を使わない場合は削除できます。

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

### 登録済み配信先へのテスト配信

1. LIFFから管理画面へログインします。
2. チャネル管理画面（`/liff/channels`）でチャネルを登録・有効化し、接続とWebhookを確認します。
3. アカウント管理画面（`/liff/account`）で本人の配信先を登録・有効化し、対象のLINE公式アカウントを友だち追加します。画面で友だち状態と配信可否を確認します。
4. 配信画面（`/liff/deliveries`）でチャネルと配信先を選び、本文をプレビューしてから送信します。
5. 送信結果を確認します。結果不明の場合は同じ操作の状態を確認し、新しい送信を自動で始めません。受取確認を要求した場合は、送信結果とは別に確認状態を追跡します。

配信APIは有効なownerセッションを必要とします。プレビュー、送信、状態確認のPOSTでは、設定済みHTTPS originと`Origin`ヘッダーの完全一致、およびCSRF tokenを検証します。

## スマートフォンからの確認

ngrokの開発用ドメインを使うと、スマートフォンのLINEアプリからローカルのFrontendと`/api`へHTTPSでアクセスできます。

起動後は、設定した開発用ドメイン（例: `https://your-domain.ngrok-free.app`）でFrontendを確認できます。`/api`はViteの既存proxyを経由してBackendへ転送されます。文章やtokenをトンネル側に記録しないため、ngrokのHTTP inspectionは無効化しており、ローカル検査画面も公開しません。

管理画面と配信APIはownerセッション、文章判定ラボはMini App ID tokenのBearer認証、Webhookはチャネル別の署名検証で保護します。Compose起動中は開発用HTTPSトンネルも有効になるため、公開URLを共有せず、利用後は`docker compose down`で全サービスとトンネルを停止します。ngrokのauthtokenはLINEのチャネル資格情報とは別の秘密情報として`.env`だけで管理します。

## ローカル品質チェック

BackendはRuffでLint・import整理・整形を行います。FrontendはESLintとtypescript-eslintの`recommendedTypeChecked`で型情報を使うLint、React Hooksの呼び出し規則と依存配列の検査、Prettierで整形、`tsc -b`で型検査を行います。ESLintとPrettierの整形ルールは`eslint-config-prettier`で競合を防ぎます。

設定は`backend/pyproject.toml`、`frontend/eslint.config.mjs`、`frontend/.prettierrc.json`が正本です。Ruffは`backend/requirements-dev.txt`、Frontendのツールは`devDependencies`とlockfileでバージョンを固定します。BackendのDockerイメージはローカル開発用なので開発依存もインストールします。

### 初回・依存更新後の準備

リポジトリ直下で実行します。Frontendの既存`node_modules` volumeにも、更新したlockfileの依存を反映します。

```bash
docker compose build frontend backend
docker compose run --rm --no-deps frontend npm ci
```

### 検査

```bash
# 両サービスの静的チェック
sh scripts/check.sh

# サービス単位で検査
sh scripts/check.sh frontend
sh scripts/check.sh backend
```

このスクリプトはファイルを修正せず、不合格なら非ゼロで終了します。DB・アプリサーバー・ngrokは起動しません。LINEの実資格情報は検査に不要です。Composeの通常の設定読込は行うため、未設定の環境変数について警告が出る場合があります。

Frontendでは`npm run check`が`lint`・`format:check`・`typecheck`を順に実行します。各コマンドは`docker compose run --rm --no-deps frontend npm run lint`のように個別実行できます。Backendでは`ruff check .`と`ruff format --check .`を実行します。Pythonの静的型検査はRuffの対象外です。

対象は両サービスのソース・テスト・設定です。Backendのmigrationも検査します。依存パッケージ、ビルド成果物、キャッシュ、coverageは除外し、Frontendのlockfileはnpmに整形を任せます。README、spec、スキルなどのMarkdownは今回の自動整形対象に含めません。

### 修正

```bash
docker compose run --rm --no-deps frontend npm run lint:fix
docker compose run --rm --no-deps frontend npm run format
docker compose run --rm --no-deps backend ruff check --fix .
docker compose run --rm --no-deps backend ruff format .
```

修正後は差分を確認して、静的チェックと変更に対応するテストを再実行します。Ruffの`--unsafe-fixes`は標準手順に含めません。Hooksの依存配列やPromiseの指摘は、通信の再実行・エラー処理への影響を確認して修正します。指摘を消すためにルールを一括無効化せず、局所的な例外が必要なら理由をコードへ記載します。

テストに限り、非同期APIのstubの契約を保つため`require-await`を無効にします。`unbound-method`はVitest用ルールへ置き換え、`expect`へ渡す関数参照と誤った非束縛呼出しを区別します。型情報を使う他のルールはテストでも有効です。

### 開発フローへの組み込み

GitHub Actionsは使用せず、ローカルの実行結果を完了・公開の判断に使います。

| 変更範囲 | 必須の静的チェック |
| --- | --- |
| Frontendのコード・テスト・設定・依存 | `sh scripts/check.sh frontend` |
| Backendのコード・テスト・設定・依存 | `sh scripts/check.sh backend` |
| 両サービス、共通チェックやその運用規則 | `sh scripts/check.sh` |
| 静的チェックの運用を変えない文書のみ | 実行不要。対象外の理由を記録 |

- `kiro-impl`は実装後、`READY_FOR_REVIEW`を返す前に対象サービス全体を検査します。
- `kiro-review`は受入前に対象サービスの検査を行います。`kiro-validate-impl`でもfeature全体に対応する結果を確認します。
- `create-pr`はcommit・push・PR作成前に、公開差分全体に対応する結果を確認します。作業ツリーだけでなく公開予定のcommitも対象です。

結果にはコマンド・終了コード・対象範囲を残します。同じコード状態・設定・依存・対象範囲の実行結果だけ再利用でき、修正後は再検査します。失敗・未実施を合格扱いせず、必要なチェックが通るまで完了・公開へ進みません。静的チェックの合格は、テストやproduction buildの代わりにはなりません。

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
